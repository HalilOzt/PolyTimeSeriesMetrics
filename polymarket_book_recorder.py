#!/usr/bin/env python3
"""Save complete Polymarket order-book snapshots at a fixed interval.

Normal use on Windows: edit recorder_config.json, then double-click RUN_RECORDER.bat.
The output is JSON Lines: one self-contained JSON object per recorded snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "recorder_config.json"
GAMMA_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
USER_AGENT = "polymarket-five-second-book-recorder/2.0"


@dataclass(frozen=True)
class Asset:
    asset_id: str
    identifier_kind: str
    market_id: str
    market_slug: str
    condition_id: str | None
    question: str
    outcome: str

    @property
    def book_id(self) -> str:
        return f"{self.market_id}:{self.outcome}"


@dataclass
class Book:
    bids: dict[str, float]
    asks: dict[str, float]
    ready: bool = False
    last_update_at_utc: str | None = None
    exchange_timestamp: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def event_slug(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("event_url cannot be empty")
    if "://" not in value:
        return value.strip("/")
    parsed = urllib.parse.urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError(f"Cannot find an event slug in {value!r}")
    return parts[-1]


def list_value(value: Any) -> list[Any]:
    """Gamma sometimes returns array fields encoded as JSON strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return decoded if isinstance(decoded, list) else [decoded]
    return [value]


def http_json(url: str, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def fetch_event(slug: str) -> dict[str, Any]:
    url = GAMMA_EVENT_URL.format(slug=urllib.parse.quote(slug, safe=""))
    event = http_json(url)
    if not isinstance(event, dict) or not event.get("id"):
        raise RuntimeError(f"No Polymarket event found for slug {slug!r}")
    return event


def discover_assets(event: dict[str, Any]) -> list[Asset]:
    assets: list[Asset] = []
    for market in event.get("markets") or []:
        # Closed markets don't send a fresh order-book snapshot. Excluding them
        # keeps every listed book meaningful and avoids permanently empty rows
        # in events that contain old/retired submarkets.
        if market.get("closed") is True or market.get("enableOrderBook") is False:
            continue
        version = str(market.get("version") or "v1").lower()
        token_ids = [str(item) for item in list_value(market.get("clobTokenIds"))]
        position_ids = [str(item) for item in list_value(market.get("positionIds"))]

        if version == "v2" and position_ids:
            ids, identifier_kind = position_ids, "position_id"
        elif token_ids:
            ids, identifier_kind = token_ids, "token_id"
        elif position_ids:
            ids, identifier_kind = position_ids, "position_id"
        else:
            logging.warning("Skipping market %s: no streamable IDs", market.get("id"))
            continue

        outcomes = [str(item) for item in list_value(market.get("outcomes"))]
        if len(outcomes) != len(ids):
            outcomes = [f"outcome_{index}" for index in range(len(ids))]

        for asset_id, outcome in zip(ids, outcomes):
            assets.append(
                Asset(
                    asset_id=asset_id,
                    identifier_kind=identifier_kind,
                    market_id=str(market.get("id") or ""),
                    market_slug=str(market.get("slug") or ""),
                    condition_id=(
                        str(market["conditionId"]) if market.get("conditionId") else None
                    ),
                    question=str(market.get("question") or ""),
                    outcome=outcome,
                )
            )

    if not assets:
        raise RuntimeError("The event has no streamable order-book outcomes")
    return assets


def event_has_ended(event: dict[str, Any]) -> bool:
    if event.get("ended") is True:
        return True
    markets = event.get("markets") or []
    return bool(markets) and all(market.get("closed") is True for market in markets)


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid JSON in {path.name} at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    required = {"event_url", "output_file"}
    missing = sorted(name for name in required if not config.get(name))
    if missing:
        raise RuntimeError(f"Missing configuration value(s): {', '.join(missing)}")

    config.setdefault("snapshot_interval_seconds", 5)
    config.setdefault("event_status_check_seconds", 30)
    config.setdefault("stop_when_event_ends", True)
    config.setdefault("end_grace_seconds", 30)

    if float(config["snapshot_interval_seconds"]) <= 0:
        raise RuntimeError("snapshot_interval_seconds must be greater than zero")
    if float(config["event_status_check_seconds"]) <= 0:
        raise RuntimeError("event_status_check_seconds must be greater than zero")
    if float(config["end_grace_seconds"]) < 0:
        raise RuntimeError("end_grace_seconds cannot be negative")
    return config


class Recorder:
    def __init__(
        self,
        event: dict[str, Any],
        assets: list[Asset],
        output_path: Path,
        interval: float,
    ) -> None:
        self.event = event
        self.assets = assets
        self.books = {asset.asset_id: Book(bids={}, asks={}) for asset in assets}
        self.output_path = output_path
        self.interval = interval
        self.run_id = str(uuid.uuid4())
        self.connected = False
        self.first_book = asyncio.Event()
        self.snapshots_written = 0

    def process_message(self, message: dict[str, Any]) -> None:
        if isinstance(message.get("payload"), dict) and message.get("type"):
            event_type = str(message["type"])
            payload = message["payload"]
        else:
            event_type = str(message.get("event_type") or message.get("type") or "")
            payload = message

        if event_type == "book":
            asset_value = payload.get("asset_id", payload.get("assetId"))
            if asset_value is None:
                return
            asset_id = str(asset_value)
            book = self.books.get(asset_id)
            if book is None:
                return
            book.bids = self._levels(payload.get("bids") or [])
            book.asks = self._levels(payload.get("asks") or [])
            book.ready = True
            book.last_update_at_utc = utc_now()
            book.exchange_timestamp = self._timestamp(payload)
            self.first_book.set()
            return

        if event_type != "price_change":
            return

        changes = payload.get("price_changes", payload.get("priceChanges", [])) or []
        for change in changes:
            asset_value = change.get("asset_id", change.get("assetId"))
            if asset_value is None:
                continue
            asset_id = str(asset_value)
            book = self.books.get(asset_id)
            if book is None:
                continue
            side = str(change.get("side") or "").upper()
            levels = book.bids if side == "BUY" else book.asks if side == "SELL" else None
            if levels is None:
                continue
            price = str(change["price"])
            size = float(change["size"])
            if size <= 0:
                levels.pop(price, None)
            else:
                levels[price] = size
            book.ready = True
            book.last_update_at_utc = utc_now()
            book.exchange_timestamp = self._timestamp(payload)
            self.first_book.set()

    @staticmethod
    def _levels(levels: list[dict[str, Any]]) -> dict[str, float]:
        return {
            str(level["price"]): float(level["size"])
            for level in levels
            if float(level["size"]) > 0
        }

    @staticmethod
    def _timestamp(payload: dict[str, Any]) -> str | None:
        value = payload.get("timestamp")
        return str(value) if value is not None else None

    def snapshot(self, reason: str = "interval") -> dict[str, Any]:
        books: list[dict[str, Any]] = []
        for asset in self.assets:
            book = self.books[asset.asset_id]
            sorted_bids = dict(
                sorted(book.bids.items(), key=lambda pair: float(pair[0]), reverse=True)
            )
            sorted_asks = dict(
                sorted(book.asks.items(), key=lambda pair: float(pair[0]))
            )
            books.append(
                {
                    "book_id": asset.book_id,
                    "market_id": asset.market_id,
                    "market_slug": asset.market_slug,
                    "question": asset.question,
                    "outcome": asset.outcome,
                    "asset_id": asset.asset_id,
                    "identifier_kind": asset.identifier_kind,
                    "condition_id": asset.condition_id,
                    "book_ready": book.ready,
                    "last_update_at_utc": book.last_update_at_utc,
                    "exchange_timestamp": book.exchange_timestamp,
                    "bid": sorted_bids,
                    "ask": sorted_asks,
                }
            )
        return {
            "recorded_at_utc": utc_now(),
            "recorded_at_ns": time.time_ns(),
            "snapshot_interval_seconds": self.interval,
            "snapshot_reason": reason,
            "run_id": self.run_id,
            "websocket_connected": self.connected,
            "event_id": str(self.event.get("id") or ""),
            "event_slug": str(self.event.get("slug") or ""),
            "event_title": str(self.event.get("title") or ""),
            "books": books,
        }

    def append_snapshot(self, reason: str = "interval") -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        row = json.dumps(
            self.snapshot(reason),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.output_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(row + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.snapshots_written += 1
        if self.snapshots_written <= 3 or self.snapshots_written % 12 == 0:
            ready = sum(book.ready for book in self.books.values())
            logging.info(
                "Saved snapshot %d (%d/%d books ready) -> %s",
                self.snapshots_written,
                ready,
                len(self.books),
                self.output_path,
            )


async def heartbeat(websocket: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(10)
        await websocket.send("PING")


async def stream_once(
    recorder: Recorder,
    stop: asyncio.Event,
    stale_timeout: float = 45.0,
) -> None:
    async with connect(
        MARKET_WS_URL,
        ping_interval=None,
        open_timeout=20,
        close_timeout=10,
        max_queue=10_000,
        max_size=16 * 1024 * 1024,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "assets_ids": [asset.asset_id for asset in recorder.assets],
                    "type": "market",
                }
            )
        )
        recorder.connected = True
        logging.info("Connected to %d Polymarket outcome books", len(recorder.assets))
        heartbeat_task = asyncio.create_task(heartbeat(websocket, stop))
        try:
            while not stop.is_set():
                raw = await asyncio.wait_for(websocket.recv(), timeout=stale_timeout)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if raw in {"PONG", "pong", "ping"}:
                    if raw == "ping":
                        await websocket.send("pong")
                    continue
                decoded = json.loads(raw)
                messages = decoded if isinstance(decoded, list) else [decoded]
                for message in messages:
                    if isinstance(message, dict):
                        recorder.process_message(message)
        finally:
            recorder.connected = False
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


async def websocket_manager(recorder: Recorder, stop: asyncio.Event) -> None:
    delay = 1.0
    while not stop.is_set():
        try:
            await stream_once(recorder, stop)
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            recorder.connected = False
            if stop.is_set():
                return
            wait_for = delay * random.uniform(0.8, 1.2)
            logging.warning("WebSocket disconnected (%s); retrying in %.1fs", exc, wait_for)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_for)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 60.0)


async def snapshot_writer(recorder: Recorder, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(recorder.first_book.wait(), timeout=30)
    except asyncio.TimeoutError:
        logging.warning("No initial book arrived within 30 seconds; writing status snapshots")

    loop = asyncio.get_running_loop()
    next_write = loop.time()
    while not stop.is_set():
        recorder.append_snapshot()
        next_write += recorder.interval
        delay = max(0.0, next_write - loop.time())
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


async def event_status_monitor(
    slug: str,
    recorder: Recorder,
    stop: asyncio.Event,
    check_interval: float,
    end_grace: float,
) -> None:
    while not stop.is_set():
        try:
            event = await asyncio.to_thread(fetch_event, slug)
            if event_has_ended(event):
                logging.info(
                    "Polymarket reports that the event ended; recording for %.0f more seconds",
                    end_grace,
                )
                if end_grace:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=end_grace)
                    except asyncio.TimeoutError:
                        pass
                if not stop.is_set():
                    recorder.append_snapshot(reason="event_ended")
                    stop.set()
                return
        except Exception as exc:
            logging.warning("Could not check event status (%s); will try again", exc)

        try:
            await asyncio.wait_for(stop.wait(), timeout=check_interval)
        except asyncio.TimeoutError:
            pass


async def run(config: dict[str, Any]) -> None:
    slug = event_slug(str(config["event_url"]))
    event = await asyncio.to_thread(fetch_event, slug)

    if config["stop_when_event_ends"] and event_has_ended(event):
        print(f"Event: {event.get('title')} ({slug})")
        print("\nPolymarket already marks this event as ended. Nothing was recorded.")
        print("Put the URL of an upcoming/live event in recorder_config.json.")
        return

    assets = discover_assets(event)

    output_path = Path(str(config["output_file"]))
    if not output_path.is_absolute():
        output_path = BASE_DIR / output_path
    output_path = output_path.resolve()

    print(f"Event: {event.get('title')} ({slug})")
    print(f"Books: {len(assets)}")
    print(f"Snapshot interval: {float(config['snapshot_interval_seconds']):g} seconds")
    print(f"Output: {output_path}")
    for asset in assets:
        print(f"  - {asset.question} | {asset.outcome}")

    recorder = Recorder(
        event=event,
        assets=assets,
        output_path=output_path,
        interval=float(config["snapshot_interval_seconds"]),
    )
    stop = asyncio.Event()

    def request_stop(*_: Any) -> None:
        logging.info("Stop requested")
        stop.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(signal_name, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal_name, request_stop)

    tasks = [
        asyncio.create_task(websocket_manager(recorder, stop)),
        asyncio.create_task(snapshot_writer(recorder, stop)),
    ]
    if config["stop_when_event_ends"]:
        tasks.append(
            asyncio.create_task(
                event_status_monitor(
                    slug=slug,
                    recorder=recorder,
                    stop=stop,
                    check_interval=float(config["event_status_check_seconds"]),
                    end_grace=float(config["end_grace_seconds"]),
                )
            )
        )

    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"\nFinished. Wrote {recorder.snapshots_written} snapshots to:")
        print(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Configuration file (default: {DEFAULT_CONFIG.name})",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        config = load_config(parse_args().config.resolve())
        asyncio.run(run(config))
    except KeyboardInterrupt:
        return 0
    except Exception:
        logging.exception("Recorder stopped because of an error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
