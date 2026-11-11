# Polymarket Order-Book Recorder

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![WebSocket](https://img.shields.io/badge/feed-WebSocket-B7F34A)](https://docs.polymarket.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)

> Turn Polymarket's live order-book stream into durable, research-ready time series.

**[View the interactive project page](https://halilozt.github.io/PolyTimeSeriesMetrics/)** · [Quick start](#quick-start) · [Output format](#output-format) · [Architecture](#architecture)

This local-first Python recorder discovers every outcome book in a Polymarket
event, reconstructs live market depth from the CLOB WebSocket, and writes a
complete snapshot at a fixed cadence. It stops automatically when the event
ends and requires no wallet, login, private key, or Polymarket API key.

## Highlights

- **Complete market depth** — captures every bid and ask level, not only the top of book.
- **Event-wide coverage** — discovers and subscribes to every streamable outcome token.
- **Crash-resilient output** — each JSONL row is flushed and synced independently.
- **Connection-aware data** — records WebSocket state and per-book freshness timestamps.
- **Self-healing stream** — reconnects with jittered exponential backoff.
- **Low-friction operation** — run via Windows launcher, Python, or Docker Compose.

## Quick start

### Windows launcher

1. Install Python 3.11 or newer from <https://www.python.org/downloads/windows/>.
   During installation, select **Add Python to PATH**.
2. Open `recorder_config.json` in Notepad.
3. Put the desired Polymarket event URL in `event_url`.
4. Save the file.
5. Double-click `RUN_RECORDER.bat`.

The launcher creates a private Python environment and installs the required
package automatically. Keep the command window open and prevent the computer
from sleeping until the match ends.

### Python

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python polymarket_book_recorder.py
```

### Docker

```bash
docker compose up --build
```

## Configuration

```json
{
  "event_url": "https://polymarket.com/sports/epl/epl-eve-che-2026-10-17",
  "output_file": "data/order_book_snapshots.jsonl",
  "snapshot_interval_seconds": 5,
  "stop_when_event_ends": true,
  "event_status_check_seconds": 30,
  "end_grace_seconds": 30
}
```

Normally, edit only `event_url` and optionally `output_file`. Leave the other
settings unchanged for five-second recording and automatic stopping. After the
event ends, the program allows 30 seconds for final WebSocket changes and then
writes a final snapshot.

The example event in the repository may already be marked as ended by
Polymarket. In that case the program exits normally without recording. Replace
it with an upcoming or currently live event URL for a new run.

## Output format

The output is `data/order_book_snapshots.jsonl`. JSON Lines is used so every
line is an independent, valid JSON snapshot and completed rows remain usable if
the program or computer stops unexpectedly.

Each snapshot has this structure (shortened example):

```json
{
  "recorded_at_utc": "2026-10-17T18:15:05.000+00:00",
  "snapshot_interval_seconds": 5.0,
  "websocket_connected": true,
  "event_slug": "example-event",
  "books": [
    {
      "question": "Will Team A win?",
      "outcome": "Yes",
      "book_ready": true,
      "bid": {
        "0.48": 125.5,
        "0.47": 300.0
      },
      "ask": {
        "0.49": 80.0,
        "0.50": 210.25
      }
    }
  ]
}
```

The keys inside `bid` and `ask` are prices, and their values are volumes.
Bids are ordered from highest to lowest price; asks are ordered from lowest to
highest price. Each snapshot also contains IDs and timestamps for identifying
the market and checking data freshness.

If `websocket_connected` is false, that row represents the most recently known
book while the program is reconnecting. `last_update_at_utc` shows when that
particular book last changed.

## Architecture

```text
Event URL
   │
   ├─ Gamma API ──► discover outcome tokens
   │
   ├─ CLOB WebSocket ──► full books + price deltas
   │                            │
   │                     in-memory books
   │                            │
   └─ event status poller       ├─ every 5s ──► fsynced JSONL snapshot
            │                   │
            └─ end + grace ─────┴─► final snapshot + clean stop
```

The recorder runs the WebSocket manager, monotonic snapshot writer, and event
status monitor as independent `asyncio` tasks. This keeps recording cadence
separate from network traffic and lifecycle checks.

## Repository layout

```text
polymarket_book_recorder.py  Core discovery, stream, book, and writer logic
recorder_config.json         Event URL, output path, cadence, and stop policy
RUN_RECORDER.bat             One-click Windows setup and launcher
Dockerfile                   Minimal Python runtime
docker-compose.yml           Persistent local data volume
docs/                        Interactive GitHub Pages case study
```

## Stopping manually

Press `Ctrl+C` in the recorder window. The file contains every completed line
written before the stop.

## Important local-computer limitation

The computer must remain powered on, connected to the internet, and awake.
Windows sleep stops the recorder. Turning off only the screen is fine.

## License

[MIT](LICENSE) © 2026 Halil Ozt
