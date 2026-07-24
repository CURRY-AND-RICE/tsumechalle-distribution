const state = { data: null, binSize: 50 };

const formatDate = (value) => {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Tokyo",
  }).format(new Date(value));
};

function setStatus(status) {
  document.querySelector("#next-update").textContent = formatDate(status?.nextScheduledUpdateAt);
  const node = document.querySelector("#update-status");
  if (!status) {
    node.textContent = "状態を取得できません";
    node.className = "status bad";
  } else if (status.lastAttemptSucceeded) {
    node.textContent = "成功";
    node.className = "status good";
  } else {
    node.textContent = "失敗（前回成功データを表示）";
    node.className = "status bad";
  }
}

function buildBins(ratings, binSize) {
  if (!ratings.length) return [];
  const min = Math.floor(ratings.at(-1).rating / binSize) * binSize;
  const max = Math.floor(ratings[0].rating / binSize) * binSize;
  const bins = [];
  for (let from = min; from <= max; from += binSize) bins.push({ from, count: 0 });
  for (const row of ratings) bins[Math.floor((row.rating - min) / binSize)].count += row.count;
  return bins;
}

function renderHistogram(highlightRating = null) {
  const chart = document.querySelector("#histogram");
  chart.replaceChildren();
  const bins = buildBins(state.data.ratings, state.binSize);
  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);
  for (const bin of bins) {
    const bar = document.createElement("div");
    bar.className = "bar";
    if (highlightRating !== null && highlightRating >= bin.from && highlightRating < bin.from + state.binSize) {
      bar.classList.add("highlight");
    }
    bar.style.height = `${Math.max(1, (bin.count / maxCount) * 100)}%`;
    bar.title = `${bin.from}–${bin.from + state.binSize - 1}: ${bin.count.toLocaleString("ja-JP")}人`;
    chart.append(bar);
  }
}

function findPosition(rating) {
  const rows = state.data.ratings;
  let above = 0;
  for (const row of rows) {
    if (row.rating > rating) above += row.count;
    else if (row.rating === rating) {
      return { rank: row.rank, same: row.count, exact: true };
    } else break;
  }
  return { rank: above + 1, same: 0, exact: false };
}

document.querySelector("#rating-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.data) return;
  const rating = Number.parseInt(document.querySelector("#rating-input").value, 10);
  if (!Number.isInteger(rating) || rating < 0 || rating > 10000) return;
  const position = findPosition(rating);
  const percentile = Math.min(100, position.rank / state.data.totalUsers * 100);
  document.querySelector("#result").innerHTML =
    `<strong>${position.rank.toLocaleString("ja-JP")}位</strong> / ${state.data.totalUsers.toLocaleString("ja-JP")}人 ` +
    `（上位 ${percentile.toFixed(1)}%）` +
    (position.exact ? ` · 同レーティング ${position.same.toLocaleString("ja-JP")}人` : " · レーティング間から算出");
  renderHistogram(rating);
});

async function initialize() {
  const [dataResult, statusResult] = await Promise.allSettled([
    fetch("./data/latest.json", { cache: "no-cache" }).then((response) => {
      if (!response.ok) throw new Error("distribution unavailable");
      return response.json();
    }),
    fetch("./data/status.json", { cache: "no-cache" }).then((response) => {
      if (!response.ok) throw new Error("status unavailable");
      return response.json();
    }),
  ]);
  setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
  if (dataResult.status === "rejected") {
    document.querySelector("#updated-at").textContent = "未取得";
    document.querySelector("#chart-message").textContent = "ランキングデータはまだ取得されていません。";
    return;
  }
  state.data = dataResult.value;
  document.querySelector("#updated-at").textContent = formatDate(state.data.collectedAt);
  document.querySelector("#total-users").textContent = `${state.data.totalUsers.toLocaleString("ja-JP")} 人`;
  document.querySelector("#chart-message").textContent = `幅 ${state.binSize} ごとの人数。棒に触れると人数を確認できます。`;
  renderHistogram();
}

initialize();

