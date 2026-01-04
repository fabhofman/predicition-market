
const BASE = "https://prediction-markets-clean.onrender.com";


function startSmartPoll(fn, { baseMs = 5000, maxMs = 60000, runImmediately = true } = {}) {
  let timer = null;
  let inFlight = false;
  let delay = baseMs;

  async function tick() {
    if (document.visibilityState !== "visible") {
      schedule(baseMs);
      return;
    }
    if (inFlight) {
      schedule(baseMs);
      return;
    }

    inFlight = true;
    try {
      await fn();
      delay = baseMs;
    } catch (e) {
      console.warn("[poll] error:", e);
      delay = Math.min(maxMs, Math.floor(delay * 1.7));
    } finally {
      inFlight = false;
      schedule(delay);
    }
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(tick, ms);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") schedule(250);
  });

  if (runImmediately) tick();
  else schedule(baseMs);

  return () => clearTimeout(timer);
}

function mapWithConcurrency(items, limit, mapper) {
  return new Promise((resolve) => {
    const results = new Array(items.length);
    let index = 0;
    let active = 0;

    function launchNext() {
      if (index >= items.length && active === 0) {
        resolve(results);
        return;
      }

      while (active < limit && index < items.length) {
        const current = index++;
        active++;
        Promise.resolve()
          .then(() => mapper(items[current], current))
          .then((res) => {
            results[current] = res;
          })
          .catch((err) => {
            results[current] = err;
          })
          .finally(() => {
            active--;
            launchNext();
          });
      }
    }

    launchNext();
  });
}

/* Fetch helper*/
async function fetchJson(url, opts = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, { ...opts, signal: controller.signal });

    let data = {};
    try {
      data = await res.json();
    } catch {
      const txt = await res.text().catch(() => "");
      data = { message: txt };
    }

    if (!res.ok) {
      const msg =
        data?.detail ||
        data?.message ||
        (typeof data === "string" ? data : "") ||
        `HTTP ${res.status}`;
      throw new Error(msg);
    }

    return data;
  } finally {
    clearTimeout(t);
  }
}

function getUser() {
  const stored = localStorage.getItem("username");
  if (!stored) return null;
  const trimmed = stored.trim();
  return trimmed.length ? trimmed : null;
}

function getMarketIdFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const id = parseInt(params.get("id"), 10);
  return Number.isFinite(id) ? id : null;
}

/** Cache user points so we don't spam /users/login */
let userCache = { username: null, points: null, ts: 0 };

async function refreshUserHeader({ force = false } = {}) {
  const username = getUser();
  const headerUserInfo = document.getElementById("header-user-info");
  const authCta = document.getElementById("auth-cta");
  const currentUserEl = document.getElementById("current-user");

  if (!username) {
    if (headerUserInfo) headerUserInfo.textContent = "Guest · — pts";
    if (authCta) {
      authCta.textContent = "Log in";
      authCta.href = "login.html";
      authCta.classList.remove("btn-ghost");
      authCta.classList.add("btn-plain");
    }
    if (currentUserEl) currentUserEl.textContent = "";
    userCache = { username: null, points: null, ts: 0 };
    return;
  }

  const now = Date.now();
  if (!force && userCache.username === username && now - userCache.ts < 30_000) {
    if (headerUserInfo && userCache.points != null) {
      headerUserInfo.textContent = `${username} · ${Number(userCache.points).toFixed(0)} pts`;
    }
    if (authCta) {
      authCta.textContent = "Log out";
      authCta.href = "logout.html";
      authCta.classList.add("btn-ghost");
      authCta.classList.remove("btn-plain");
    }
    if (currentUserEl) currentUserEl.textContent = `Logged in as ${username}`;
    return;
  }

  try {
    const data = await fetchJson(
      `${BASE}/users/login?username=${encodeURIComponent(username)}`,
      { method: "POST" },
      8000
    );

    userCache = { username: data.username, points: data.points, ts: now };

    if (currentUserEl) currentUserEl.textContent = `Logged in as ${data.username}`;
    if (headerUserInfo) headerUserInfo.textContent = `${data.username} · ${data.points.toFixed(0)} pts`;
    if (authCta) {
      authCta.textContent = "Log out";
      authCta.href = "logout.html";
      authCta.classList.add("btn-ghost");
      authCta.classList.remove("btn-plain");
    }
  } catch (err) {
    console.error("Header refresh failed:", err);
    if (headerUserInfo) headerUserInfo.textContent = `${username} | …`;
  }
}

async function login() {
  const username = document.getElementById("username-input")?.value?.trim();
  if (!username) {
    alert("Please enter a username");
    return;
  }

  try {
    const data = await fetchJson(
      `${BASE}/users/login?username=${encodeURIComponent(username)}`,
      { method: "POST" },
      8000
    );

    localStorage.setItem("username", username);
    userCache = { username: data.username, points: data.points, ts: Date.now() };

    const currentUserEl = document.getElementById("current-user");
    if (currentUserEl) currentUserEl.textContent = `Logged in as ${data.username}`;

    const headerUserInfo = document.getElementById("header-user-info");
    if (headerUserInfo) headerUserInfo.textContent = `${data.username} | ${data.points.toFixed(0)} points`;

    if (typeof fetchMarkets === "function") fetchMarkets();
    const urlParams = new URLSearchParams(window.location.search);
    const redirect = urlParams.get("redirect");
    if (redirect) {
      window.location.href = decodeURIComponent(redirect);
    } else if (window.location.pathname.includes("login.html")) {
      window.location.href = "index.html";
    }
  } catch (err) {
    console.error("Login failed:", err);
    alert(err.message || "Login failed. Please try again.");
  }
}


function storeMarketHint(marketId, marketName) {
  try {
    sessionStorage.setItem(
      `market_hint:${marketId}`,
      JSON.stringify({ marketId, name: marketName, ts: Date.now() })
    );
  } catch {}
}

function readMarketHint(marketId, maxAgeMs = 5 * 60_000) {
  try {
    const raw = sessionStorage.getItem(`market_hint:${marketId}`);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || data.marketId !== marketId) return null;
    if (Date.now() - (data.ts || 0) > maxAgeMs) return null;
    return data;
  } catch {
    return null;
  }
}

function wireTradeNav(container) {
  if (!container) return;
  if (container.dataset.tradeNavWired === "1") return;
  container.dataset.tradeNavWired = "1";

  container.addEventListener("click", (e) => {
    const card = e.target.closest(".market-card");
    if (!card) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

    const marketId = parseInt(card.dataset.marketId, 10);
    const marketName = card.dataset.marketName ? decodeURIComponent(card.dataset.marketName) : null;

    if (Number.isFinite(marketId) && marketName) {
      storeMarketHint(marketId, marketName);
      window.location.href = `markets.html?id=${marketId}`;
    }
  });
}

const marketProbabilityCache = {};
const previewCache = { yes: {}, no: {} };
let selectedSide = "yes";
let selectedOrderMode = "points";
const positionCache = {};

async function fetchMarketProbability(marketId, username) {
  const data = await fetchJson(
    `${BASE}/markets/preview?market_id=${marketId}&username=${encodeURIComponent(
      username
    )}&quantity=1&yesOrNo=yes`,
    {},
    8000
  );

  return Number(data.current_price);
}

async function fetchMarkets() {
  const username = getUser();
  const container = document.getElementById("markets");
  if (!container) return;

  if (!container.dataset.loadedOnce) {
    container.innerHTML = `<p class="text-muted">Loading markets…</p>`;
  }

  if (!username) {
    container.dataset.loadedOnce = "1";
    container.classList.add("is-empty");
    container.innerHTML =
      '<div class="empty-state"><p>Please log in to view live markets.</p><a class="btn-primary" href="login.html">Log in</a></div>';
    return;
  }

  container.classList.remove("is-empty");

  const data = await fetchJson(
    `${BASE}/markets/for_user?username=${encodeURIComponent(username)}`,
    {},
    8000
  );

  container.dataset.loadedOnce = "1";

  if (data?.length && container.dataset.state !== "populated") {
    container.innerHTML = "";
    container.dataset.state = "populated";
  }

  if (!Array.isArray(data) || data.length === 0) {
    container.innerHTML = "<p>No markets found.</p>";
    return;
  }

  const existingCards = new Map(
    Array.from(container.querySelectorAll(".market-card")).map((card) => [
      Number(card.dataset.marketId),
      card,
    ])
  );

  const seen = new Set();

  const probabilityResults = await mapWithConcurrency(
    data,
    6,
    (m) =>
      fetchMarketProbability(m.id, username).catch((err) => {
        console.warn("probability fetch failed", err);
        return null;
      })
  );

  data.forEach((m, idx) => {
    const probability = probabilityResults[idx];
    const previous = marketProbabilityCache[m.id];
    if (typeof probability === "number" && !Number.isNaN(probability)) {
      marketProbabilityCache[m.id] = probability;
    }

    const effectiveProbability =
      typeof probability === "number" && !Number.isNaN(probability)
        ? probability
        : typeof previous === "number" && !Number.isNaN(previous)
          ? previous
          : null;

    const probPercent =
      typeof effectiveProbability === "number" && !Number.isNaN(effectiveProbability)
        ? (effectiveProbability * 100).toFixed(1)
        : "…";

    const trendClass =
      typeof probability === "number" && typeof previous === "number"
        ? probability > previous
          ? "prob-up"
          : probability < previous
            ? "prob-down"
            : ""
        : "";

    let card = existingCards.get(m.id);
    if (!card) {
      card = document.createElement("div");
      card.className = "market-card";
      card.dataset.marketId = m.id;
      card.innerHTML = `
        <div class="market-card-header">
          <div class="market-title"></div>
          <div class="market-prob">
            <span class="prob-label">Probability</span>
            <span class="prob-value">…</span>
          </div>
        </div>
      `;
      container.appendChild(card);
    }

    const titleEl = card.querySelector(".market-title");
    const probValEl = card.querySelector(".prob-value");

    card.dataset.marketId = m.id;
    card.dataset.marketName = encodeURIComponent(m.name);
    if (titleEl) titleEl.textContent = m.name;

    if (probValEl) {
      probValEl.textContent =
        typeof effectiveProbability === "number" && !Number.isNaN(effectiveProbability)
          ? `${probPercent}%`
          : probPercent;
    }

    card.classList.remove("prob-up", "prob-down");
    if (trendClass) {
      card.classList.add(trendClass);
      setTimeout(() => card.classList.remove("prob-up", "prob-down"), 900);
    }

    seen.add(m.id);
  });

  // Remove stale cards
  existingCards.forEach((card, id) => {
    if (!seen.has(id)) card.remove();
  });

  wireTradeNav(container);
}

async function loadMarket(idFromCaller) {
  const params = new URLSearchParams(window.location.search);
  const id = idFromCaller ?? params.get("id");

  const nameEl = document.getElementById("market-name");
  if (!id) {
    if (nameEl) nameEl.textContent = "Market not found.";
    return;
  }

  const m = await fetchJson(`${BASE}/markets/${id}`, {}, 8000);
  if (nameEl) nameEl.textContent = m.name;
}

function applyMarketHintIfPresent() {
  const marketId = getMarketIdFromUrl();
  if (!marketId) return false;

  const hint = readMarketHint(marketId);
  if (!hint?.name) return false;

  const nameEl = document.getElementById("market-name");
  if (nameEl) nameEl.textContent = hint.name;

  return true;
}

function setSide(side) {
  if (side !== "yes" && side !== "no") return;
  selectedSide = side;

  document.querySelectorAll(".side-pill").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.side === side);
  });

  const buyBtn = document.getElementById("buy-button");
  const sellBtn = document.getElementById("sell-button");
  if (buyBtn) buyBtn.textContent = `Buy ${side.toUpperCase()}`;
  if (sellBtn) sellBtn.textContent = `Sell ${side.toUpperCase()}`;

  const sideLabel = document.getElementById("preview-side-label");
  if (sideLabel) sideLabel.textContent = side.toUpperCase();

  updatePreviewForSide(side);
}

function setOrderMode(mode) {
  if (mode !== "points" && mode !== "quantity") return;
  selectedOrderMode = mode;

  document.querySelectorAll(".mode-pill").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });

  const amountLabel = document.getElementById("amount-label");
  const amountHint = document.getElementById("amount-hint");
  const amountInput = document.getElementById("order-amount");
  const rowLabel = document.getElementById("preview-row-label");
  const subRowLabel = document.getElementById("preview-sub-row-label");

  if (amountLabel) amountLabel.textContent = mode === "points" ? "Amount" : "Contracts";
  if (amountHint) amountHint.textContent = mode === "points" ? "points" : "contracts";
  if (amountInput) {
    amountInput.value = amountInput.value || (mode === "points" ? 10 : 1);
    amountInput.min = mode === "points" ? "1" : "1";
  }

  if (rowLabel) rowLabel.textContent = mode === "points" ? "Contracts" : "Points";
  if (subRowLabel) subRowLabel.textContent = mode === "points" ? "Spend / Receive" : "Contracts";

  updatePreviewForSide(selectedSide);
}

async function updatePreviewForSide(side) {
  const username = getUser();
  if (!username) {
    const redirect = encodeURIComponent(window.location.href);
    window.location.href = `login.html?redirect=${redirect}`;
    return;
  }
  const marketId = getMarketIdFromUrl();
  const amountInput = document.getElementById("order-amount");

  const costEl = document.getElementById("preview-cost");
  const oddsEl = document.getElementById("preview-odds");
  const payoutEl = document.getElementById("preview-payout");
  const contractsEl = document.getElementById("preview-contracts");
  const previewSideLabel = document.getElementById("preview-side-label");

  const applyValues = (payload) => {
    const yesPrice = payload && Number.isFinite(Number(payload.current_price)) ? Number(payload.current_price) : null;
    const sidePrice = yesPrice === null ? null : side === "yes" ? yesPrice : 1 - yesPrice;

    if (previewSideLabel) previewSideLabel.textContent = side.toUpperCase();

    if (oddsEl) oddsEl.textContent = sidePrice !== null ? `${(sidePrice * 100).toFixed(1)}%` : "—";

    const payout = payload && Number.isFinite(Number(payload.payout)) ? Number(payload.payout) : null;
    if (payoutEl) payoutEl.textContent = payout !== null ? `${payout.toFixed(0)} pts` : "—";

    const qty = payload && Number.isFinite(Number(payload.quantity)) ? Number(payload.quantity) : payout;
    const cost = payload && Number.isFinite(Number(payload.order_cost)) ? Number(payload.order_cost) : null;

    if (contractsEl) {
      const label = document.getElementById("preview-row-label");
      if (label) label.textContent = selectedOrderMode === "points" ? "Contracts" : "Points";
      const displayValue = selectedOrderMode === "points" ? qty : cost;
      if (displayValue !== null) {
        const formatted = selectedOrderMode === "points" ? displayValue.toFixed(0) + "x" : displayValue.toFixed(2) + " pts";
        contractsEl.textContent = formatted;
      } else {
        contractsEl.textContent = "—";
      }
    }

    if (costEl) {
      const subLabel = document.getElementById("preview-sub-row-label");
      if (subLabel) subLabel.textContent = selectedOrderMode === "points" ? "Spend / Receive" : "Contracts";
      const displayValue = selectedOrderMode === "points" ? cost : qty;
      if (displayValue !== null) {
        const formatted = selectedOrderMode === "points" ? displayValue.toFixed(2) + " pts" : displayValue.toFixed(0) + "x";
        costEl.textContent = formatted;
      } else {
        costEl.textContent = "—";
      }
    }

    if (marketId) {
      const mainPriceEl = document.getElementById("current-market-price");
      const currentYesPriceEl = document.getElementById("current-yes-price");
      const currentNoPriceEl = document.getElementById("current-no-price");
      const sideYesValueEl = document.getElementById("side-yes-value");
      const sideNoValueEl = document.getElementById("side-no-value");

      if (mainPriceEl && yesPrice !== null) mainPriceEl.textContent = (yesPrice * 100).toFixed(1) + "%";
      if (currentYesPriceEl && yesPrice !== null) currentYesPriceEl.textContent = (yesPrice * 100).toFixed(2) + "%";
      if (currentNoPriceEl && yesPrice !== null) currentNoPriceEl.textContent = (100 - yesPrice * 100).toFixed(2) + "%";
      if (sideYesValueEl && yesPrice !== null) sideYesValueEl.textContent = (yesPrice * 100).toFixed(1) + "%";
      if (sideNoValueEl && yesPrice !== null) sideNoValueEl.textContent = (100 - yesPrice * 100).toFixed(1) + "%";
    }
  };

  if (!amountInput || !marketId) {
    applyValues(previewCache[side]?.[selectedOrderMode]);
    return;
  }

  const amountValue = parseFloat(amountInput.value);
  const amount = Number.isFinite(amountValue) ? Math.max(1, amountValue) : NaN;
  if (!Number.isFinite(amount) || amount <= 0) {
    applyValues(previewCache[side]?.[selectedOrderMode]);
    return;
  }

  try {
    const data = await fetchJson(
      `${BASE}/markets/preview?market_id=${marketId}&username=${encodeURIComponent(
        username
      )}&${selectedOrderMode === "points" ? "points" : "quantity"}=${amount}&yesOrNo=${side}`,
      {},
      8000
    );

    const parsed = {
      order_cost: Number(data.order_cost),
      new_price: Number(data.new_price),
      current_price: Number(data.current_price),
      quantity: Number(data.quantity),
      payout: Number(data.payout),
    };

    previewCache[side][selectedOrderMode] = parsed;
    applyValues(parsed);
    refreshMarketPosition();
  } catch (err) {
    console.warn(`Preview failed for ${side}:`, err);
    if (previewCache[side]?.[selectedOrderMode]) {
      applyValues(previewCache[side][selectedOrderMode]);
    }
  }
}

async function refreshMarketPosition({ force = false } = {}) {
  const username = getUser();
  const marketId = getMarketIdFromUrl();
  const yesEl = document.getElementById("position-yes");
  const noEl = document.getElementById("position-no");

  if (!yesEl || !noEl) return;

  if (!username || !marketId) {
    yesEl.textContent = "—";
    noEl.textContent = "—";
    return;
  }

  const cacheKey = `${username}-${marketId}`;
  const cached = positionCache[cacheKey];
  if (!force && cached && Date.now() - cached.ts < 4000) {
    yesEl.textContent = `${cached.yes.toFixed(0)}x`;
    noEl.textContent = `${cached.no.toFixed(0)}x`;
    return;
  }

  try {
    const data = await fetchJson(
      `${BASE}/users/${encodeURIComponent(username)}/portfolio`,
      {},
      8000
    );

    const positions = Array.isArray(data?.positions)
      ? data.positions
      : Array.isArray(data?.portfolio)
      ? data.portfolio
      : [];

    const pos = positions.find((p) => Number(p.market_id) === Number(marketId)) || null;

    const yesQty = pos?.yes != null ? Number(pos.yes) : 0;
    const noQty = pos?.no != null ? Number(pos.no) : 0;

    yesEl.textContent = `${yesQty.toFixed(0)}x`;
    noEl.textContent = `${noQty.toFixed(0)}x`;

    positionCache[cacheKey] = { yes: yesQty, no: noQty, ts: Date.now() };
  } catch (err) {
    console.warn("Position refresh failed", err);
  }
}

async function updatePreview() {
  await updatePreviewForSide(selectedSide);
}

async function buy(side = selectedSide) {
  const username = getUser();
  if (!username) {
    window.location.href = `login.html?redirect=${encodeURIComponent(window.location.href)}`;
    return;
  }
  const marketId = getMarketIdFromUrl();
  const amountValue = parseFloat(document.getElementById("order-amount")?.value);
  const amount = Number.isFinite(amountValue) ? Math.max(1, amountValue) : NaN;

  if (!marketId) return showResult("Missing market id.", "error");
  if (!amount || amount <= 0 || isNaN(amount)) return showResult("Enter a valid points amount.", "error");

  try {
    const data = await fetchJson(
      `${BASE}/markets/buy?market_id=${marketId}&username=${encodeURIComponent(
        username
      )}&${selectedOrderMode === "points" ? "points" : "quantity"}=${amount}&yesOrNo=${side}`,
      { method: "POST" },
      12000
    );

    const usedQty = data?.quantity != null ? Number(data.quantity).toFixed(0) : amount;
    showResult(
      `Successfully bought ${usedQty} ${side.toUpperCase()}! New balance: ${
        data.new_balance != null ? Number(data.new_balance).toFixed(2) : "N/A"
      }`,
      "success"
    );

    if (data.new_balance != null) {
      userCache = { username, points: data.new_balance, ts: Date.now() };
      const headerUserInfo = document.getElementById("header-user-info");
      if (headerUserInfo) headerUserInfo.textContent = `${username} | ${Number(data.new_balance).toFixed(0)} points`;
    }

    Object.keys(positionCache).forEach((k) => delete positionCache[k]);
    await Promise.all([
      loadMarket(marketId),
      updatePreview(),
      refreshMarketPosition({ force: true }),
    ]);
  } catch (err) {
    showResult(`Trade failed: ${err.message}`, "error");
  }
}

async function sell(side = selectedSide) {
  const username = getUser();
  if (!username) {
    window.location.href = `login.html?redirect=${encodeURIComponent(window.location.href)}`;
    return;
  }
  const marketId = getMarketIdFromUrl();
  const amountValue = parseFloat(document.getElementById("order-amount")?.value);
  const amount = Number.isFinite(amountValue) ? Math.max(1, amountValue) : NaN;

  if (!marketId) return showResult("Missing market id.", "error");
  if (!amount || amount <= 0 || isNaN(amount)) return showResult("Enter a valid points amount.", "error");

  try {
    const data = await fetchJson(
      `${BASE}/markets/sell?market_id=${marketId}&username=${encodeURIComponent(
        username
      )}&${selectedOrderMode === "points" ? "points" : "quantity"}=${amount}&yesOrNo=${side}`,
      { method: "POST" },
      12000
    );

    const usedQty = data?.quantity != null ? Number(data.quantity).toFixed(0) : amount;
    showResult(
      `Successfully sold ${usedQty} ${side.toUpperCase()}! New balance: ${
        data.new_balance != null ? Number(data.new_balance).toFixed(2) : "N/A"
      }`,
      "success"
    );

    if (data.new_balance != null) {
      userCache = { username, points: data.new_balance, ts: Date.now() };
      const headerUserInfo = document.getElementById("header-user-info");
      if (headerUserInfo) headerUserInfo.textContent = `${username} | ${Number(data.new_balance).toFixed(0)} points`;
    }

    Object.keys(positionCache).forEach((k) => delete positionCache[k]);
    await Promise.all([
      loadMarket(marketId),
      updatePreview(),
      refreshMarketPosition({ force: true }),
    ]);
  } catch (err) {
    showResult(`Trade failed: ${err.message}`, "error");
  }
}

function showResult(message, type = "info") {
  const container = document.getElementById("result-container");
  if (!container) {
    const oldResult = document.getElementById("result");
    if (oldResult) oldResult.textContent = message;
    return;
  }

  const alertClass =
    type === "success" ? "alert-success" : type === "error" ? "alert-error" : "alert-info";

  container.innerHTML = `<div class="alert ${alertClass}">${message}</div>`;
  setTimeout(() => (container.innerHTML = ""), 5000);
}

async function loadPortfolio() {
  const username = getUser();
  const tbody = document.getElementById("portfolio-body") || document.getElementById("portfolio");

  if (tbody && !tbody.dataset.loadedOnce) {
    tbody.innerHTML = `<tr><td colspan='8' class="text-center" style="padding: 40px;">
      <div class="text-muted">Loading portfolio…</div></td></tr>`;
  }

  const d = await fetchJson(`${BASE}/users/${encodeURIComponent(username)}/portfolio`, {}, 10000);
  if (tbody) tbody.dataset.loadedOnce = "1";

  if (!d.positions || d.positions.length === 0) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan='8' class="text-center" style="padding: 60px;">
        <div class="empty-state">
          <h3>No positions yet</h3>
          <p style="margin-top: 8px;">Start trading to build your portfolio!</p>
          <a href="index.html" class="btn btn-primary" style="margin-top: 20px; display: inline-block;">Browse Markets</a>
        </div>
      </td></tr>`;
    }
    return;
  }

  const rows = d.positions
    .map(
      (p) => `
      <tr>
        <td><strong>${p.market_name}</strong></td>
        <td>${p.yes}</td>
        <td>${p.no}</td>
        <td>${
          p.current_value != null && Number.isFinite(Number(p.current_value))
            ? Number(p.current_value).toFixed(2)
            : "—"
        }</td>
      </tr>
    `
    )
    .join("");

  if (tbody.tagName.toLowerCase() === "tbody") {
    tbody.innerHTML = rows;
  } else {
    tbody.innerHTML = `
      <tr><th>Market</th><th>YES</th><th>NO</th><th>PNL</th></tr>
      ${rows}
    `;
  }
}

window.startSmartPoll = startSmartPoll;
window.refreshUserHeader = refreshUserHeader;
window.applyMarketHintIfPresent = applyMarketHintIfPresent;

window.fetchMarkets = fetchMarkets;
window.loadMarket = loadMarket;
window.refreshMarketPosition = refreshMarketPosition;

window.updatePreviewForSide = updatePreviewForSide;
window.updatePreview = updatePreview;
window.setSide = setSide;
window.setOrderMode = setOrderMode;

window.buy = buy;
window.sell = sell;

window.login = login;
window.loadPortfolio = loadPortfolio;
