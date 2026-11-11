const captures = {
  brighton: {
    name: "Brighton",
    bid: [0.68, 0.68, 0.68, 0.68, 0.68, 0.68, 0.68],
    ask: [0.69, 0.69, 0.69, 0.69, 0.69, 0.69, 0.69],
    bidSize: [163584.57, 163584.57, 163584.57, 163584.57, 164764.57, 164764.57, 164764.57],
    askSize: [1234298.82, 1234298.82, 1234293.02, 1223687.51, 1223693.51, 1223693.51, 1223693.51]
  },
  draw: {
    name: "Draw",
    bid: [0.18, 0.18, 0.18, 0.18, 0.18, 0.18, 0.18],
    ask: [0.19, 0.19, 0.19, 0.19, 0.19, 0.19, 0.19],
    bidSize: [768649.32, 768649.32, 768649.32, 760954.43, 768560.43, 768560.43, 768560.43],
    askSize: [72629.70, 72629.70, 72629.70, 72624.43, 72624.43, 72624.43, 73952.53]
  },
  palace: {
    name: "Crystal Palace",
    bid: [0.13, 0.13, 0.13, 0.13, 0.13, 0.13, 0.13],
    ask: [0.14, 0.14, 0.14, 0.14, 0.14, 0.14, 0.14],
    bidSize: [16781.42, 16781.42, 16781.42, 16781.42, 16781.42, 16781.42, 16781.42],
    askSize: [683319.57, 684419.57, 683319.57, 683319.57, 683319.57, 683319.57, 683319.57]
  }
};

const times = ["12:20:37", "12:20:42", "12:20:47", "12:20:52", "12:20:57", "12:21:02", "12:21:07"];
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const ns = "http://www.w3.org/2000/svg";
let activeMarket = "brighton";
let activeIndex = 0;
let playTimer = null;

const formatNumber = value => new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
const cents = value => `${(value * 100).toFixed(value * 100 % 1 ? 1 : 0)}¢`;

function svgNode(tag, attributes = {}) {
  const node = document.createElementNS(ns, tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function pointsFor(values, min, max) {
  const left = 40, right = 735, top = 26, bottom = 265;
  return values.map((value, index) => ({
    x: left + (index / (values.length - 1)) * (right - left),
    y: bottom - ((value - min) / (max - min || 1)) * (bottom - top)
  }));
}

function linePath(points) {
  return points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
}

function drawChart() {
  const data = captures[activeMarket];
  const all = [...data.bid, ...data.ask];
  const padding = Math.max(0.007, (Math.max(...all) - Math.min(...all)) * 0.8);
  const min = Math.min(...all) - padding;
  const max = Math.max(...all) + padding;
  const bidPoints = pointsFor(data.bid, min, max);
  const askPoints = pointsFor(data.ask, min, max);
  const grid = $(".chart-grid");
  const axis = $(".chart-axis");
  grid.replaceChildren();
  axis.replaceChildren();

  for (let i = 0; i < 5; i += 1) {
    const y = 26 + i * ((265 - 26) / 4);
    grid.append(svgNode("line", { x1: 40, x2: 735, y1: y, y2: y }));
    const label = svgNode("text", { x: 0, y: y + 4 });
    label.textContent = cents(max - i * ((max - min) / 4));
    axis.append(label);
  }
  times.forEach((time, index) => {
    const label = svgNode("text", { x: bidPoints[index].x, y: 290, "text-anchor": index === 0 ? "start" : index === times.length - 1 ? "end" : "middle" });
    label.textContent = time.slice(3);
    axis.append(label);
  });

  $(".bid-path").setAttribute("d", linePath(bidPoints));
  $(".ask-path").setAttribute("d", linePath(askPoints));
  $(".area-path").setAttribute("d", `${linePath(bidPoints)} L${bidPoints.at(-1).x},265 L${bidPoints[0].x},265 Z`);
  const bidPoint = bidPoints[activeIndex];
  const askPoint = askPoints[activeIndex];
  const cursor = $(".cursor-line");
  cursor.setAttribute("x1", bidPoint.x);
  cursor.setAttribute("x2", bidPoint.x);
  $(".bid-point").setAttribute("cx", bidPoint.x);
  $(".bid-point").setAttribute("cy", bidPoint.y);
  $(".ask-point").setAttribute("cx", askPoint.x);
  $(".ask-point").setAttribute("cy", askPoint.y);
}

function render() {
  const data = captures[activeMarket];
  const bid = data.bid[activeIndex];
  const ask = data.ask[activeIndex];
  const spread = ask - bid;
  $("#mid-price").textContent = cents((bid + ask) / 2);
  $("#spread-value").textContent = cents(spread);
  $("#ask-price").textContent = cents(ask);
  $("#bid-price").textContent = cents(bid);
  $("#ask-size").textContent = formatNumber(data.askSize[activeIndex]);
  $("#bid-size").textContent = formatNumber(data.bidSize[activeIndex]);
  $("#book-gap").textContent = `${cents(spread)} spread`;
  $("#snapshot-label").textContent = `Snapshot ${String(activeIndex + 1).padStart(2, "0")} / 07`;
  $("#snapshot-time").textContent = `${times[activeIndex]} UTC`;
  const slider = $("#snapshot-slider");
  slider.value = activeIndex;
  const progress = (activeIndex / 6) * 100;
  slider.style.background = `linear-gradient(to right, var(--acid) 0%, var(--acid) ${progress}%, #343a34 ${progress}%, #343a34 100%)`;
  drawChart();
}

function stopPlayback() {
  if (playTimer) clearInterval(playTimer);
  playTimer = null;
  $("#play-button span").textContent = "▶";
  $("#play-button").setAttribute("aria-label", "Play capture");
}

$$('.market-tab').forEach(tab => {
  tab.addEventListener("click", () => {
    stopPlayback();
    activeMarket = tab.dataset.market;
    activeIndex = 0;
    $$('.market-tab').forEach(item => {
      const isActive = item === tab;
      item.classList.toggle("active", isActive);
      item.setAttribute("aria-selected", String(isActive));
    });
    render();
  });
});

$("#snapshot-slider").addEventListener("input", event => {
  stopPlayback();
  activeIndex = Number(event.target.value);
  render();
});

$("#play-button").addEventListener("click", () => {
  if (playTimer) {
    stopPlayback();
    return;
  }
  if (activeIndex === 6) activeIndex = 0;
  $("#play-button span").textContent = "Ⅱ";
  $("#play-button").setAttribute("aria-label", "Pause capture");
  render();
  playTimer = setInterval(() => {
    if (activeIndex === 6) {
      stopPlayback();
      return;
    }
    activeIndex += 1;
    render();
  }, 850);
});

$(".copy-command").addEventListener("click", async event => {
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(button.dataset.command);
    $("span", button).textContent = "Copied";
    setTimeout(() => { $("span", button).textContent = "Copy"; }, 1800);
  } catch {
    $("span", button).textContent = "Select";
  }
});

render();
