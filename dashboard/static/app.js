(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const fmtMoney = (v) => {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const sign = v < 0 ? "-" : "";
    return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };
  const fmtPct = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const fmtSigned = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : `${v >= 0 ? "+" : ""}${fmtMoney(v).replace("-", "")}`.replace("+-", "-");
  const signClass = (v) => (v === null || v === undefined) ? "" : v >= 0 ? "positive" : "negative";
  const fmtTime = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  // ── Renderers ──────────────────────────────────────────────────────

  function renderPortfolioAndPnl(portfolio, pnl) {
    const modeBadge = $("mode-badge");
    if (portfolio.mode === "LIVE") {
      modeBadge.textContent = "● LIVE TRADING";
      modeBadge.className = "mode-badge mode-live";
    } else if (portfolio.mode === "PAPER") {
      modeBadge.textContent = "● PAPER TRADING";
      modeBadge.className = "mode-badge mode-paper";
    } else {
      modeBadge.textContent = "● UNKNOWN";
      modeBadge.className = "mode-badge mode-unknown";
    }

    $("stat-equity").textContent = fmtMoney(portfolio.equity);
    $("stat-buying-power").textContent = fmtMoney(portfolio.buying_power);
    $("stat-cash").textContent = fmtMoney(portfolio.cash);
    $("stat-invested").textContent = fmtMoney(portfolio.invested_value);
    $("stat-exposure").textContent = portfolio.exposure_pct === null ? "—" : `${portfolio.exposure_pct.toFixed(1)}%`;

    const pnlEl = $("stat-pnl");
    pnlEl.textContent = pnl.today_pl === null ? "—" : `${fmtSigned(pnl.today_pl)} (${fmtPct(pnl.today_pl_pct)})`;
    pnlEl.className = `stat-value mono ${signClass(pnl.today_pl)}`;

    $("pnl-realized").textContent = pnl.realized_pl_today === null ? "unavailable" : fmtSigned(pnl.realized_pl_today);
    $("pnl-unrealized").textContent = pnl.unrealized_pl_total === null ? "unavailable" : fmtSigned(pnl.unrealized_pl_total);
    $("pnl-winrate").textContent = pnl.win_rate === null || pnl.win_rate === undefined ? "no closed trades yet" : `${(pnl.win_rate * 100).toFixed(0)}%`;

    if (!portfolio.connected) {
      $("last-updated").textContent = "Alpaca not connected — check ALPACA_API_KEY / ALPACA_SECRET_KEY in .env";
    }
  }

  function renderPositions(payload) {
    const tbody = document.querySelector("#positions-table tbody");
    const rows = payload.positions || [];
    if (!payload.connected) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-row">Alpaca not connected — positions unavailable</td></tr>`;
      return;
    }
    if (rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No open positions</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((p) => `
      <tr>
        <td>${p.symbol}</td>
        <td>${p.side}</td>
        <td class="mono">${p.qty}</td>
        <td class="mono">${fmtMoney(p.avg_entry_price)}</td>
        <td class="mono">${fmtMoney(p.current_price)}</td>
        <td class="mono">${fmtMoney(p.market_value)}</td>
        <td class="mono ${signClass(p.unrealized_pl)}">${fmtSigned(p.unrealized_pl)}</td>
        <td class="mono ${signClass(p.unrealized_plpc)}">${fmtPct(p.unrealized_plpc)}</td>
      </tr>
    `).join("");
  }

  function renderTrades(payload) {
    const tbody = document.querySelector("#trades-table tbody");
    const rows = payload.trades || [];
    if (!payload.connected && rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-row">Alpaca not connected — order history unavailable</td></tr>`;
      return;
    }
    if (rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-row">No recent trades</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((t) => `
      <tr title="${t.reason ? t.reason.replace(/"/g, "'") : ""}">
        <td class="mono">${fmtTime(t.ts)}</td>
        <td>${t.symbol || "—"}</td>
        <td>${t.side || "—"}</td>
        <td class="mono">${t.qty ?? "—"}</td>
        <td>${t.order_type || (t.source === "risk_gate" ? "—" : "—")}</td>
        <td class="mono">${fmtMoney(t.filled_avg_price ?? t.limit_price)}</td>
        <td><span class="badge badge-${(t.status || "UNKNOWN").toUpperCase()}">${t.status || "UNKNOWN"}</span></td>
      </tr>
    `).join("");
  }

  function renderDecisions(payload) {
    const wrap = $("decisions-list");
    const rows = payload.decisions || [];
    if (rows.length === 0) {
      wrap.innerHTML = `<div class="empty-row">No AI decisions logged yet — they appear here once the agent loop runs a cycle</div>`;
      return;
    }
    wrap.innerHTML = rows.map((d) => `
      <div class="decision-item">
        <div class="decision-head">
          <span class="decision-symbol">${d.symbol || d.decision || "Cycle"}</span>
          <span class="badge badge-${(d.status || "INFO").toUpperCase()}">${d.status || "INFO"}</span>
        </div>
        <div class="decision-reasoning">
          ${(d.reasoning || "No reasoning recorded.").slice(0, 320)}${(d.reasoning || "").length > 320 ? "…" : ""}
        </div>
        <div class="decision-time">${fmtTime(d.ts)}${d.confidence !== null && d.confidence !== undefined ? ` · confidence ${d.confidence}%` : ""}</div>
      </div>
    `).join("");
  }

  function renderRisk(payload) {
    const wrap = $("risk-list");
    const gates = payload.gates || [];
    wrap.innerHTML = gates.map((g) => `
      <div class="risk-item">
        <div>
          <div class="risk-name">${g.name}</div>
          <div class="risk-detail">${g.detail}</div>
        </div>
        <span class="badge badge-${g.status}">${g.status}</span>
      </div>
    `).join("");
  }

  function renderAgent(a) {
    const grid = $("agent-grid");
    const item = (k, v, dotClass) => `
      <div class="agent-item">
        <span class="k">${k}</span>
        <span class="v">${dotClass ? `<span class="dot ${dotClass}"></span>` : ""}${v}</span>
      </div>`;

    grid.innerHTML = [
      item("Agent", a.agent_running ? "RUNNING" : "STOPPED / UNKNOWN", a.agent_running ? "dot-on" : "dot-off"),
      item("Alpaca", a.alpaca_connected ? "CONNECTED" : "DISCONNECTED", a.alpaca_connected ? "dot-on" : "dot-off"),
      item("Database", a.database_connected ? "CONNECTED" : "ERROR", a.database_connected ? "dot-on" : "dot-off"),
      item("Risk Engine", "ACTIVE", "dot-on"),
      item("Execution Engine", a.execution_engine_ready ? "READY" : "UNAVAILABLE", a.execution_engine_ready ? "dot-on" : "dot-off"),
      item("Kill Switch", a.kill_switch_active ? "ACTIVE" : "CLEAR", a.kill_switch_active ? "dot-off" : "dot-on"),
      item("Trading Mode", (a.trading_mode || "—").toUpperCase(), null),
      item("Last Heartbeat", fmtTime(a.last_heartbeat), null),
      item("Last Analysis", fmtTime(a.last_analysis), null),
      item("Last Trade", fmtTime(a.last_trade), null),
    ].join("");
  }

  function renderAll(payload) {
    renderPortfolioAndPnl(payload.portfolio, payload.pnl);
    renderPositions(payload.positions);
    renderTrades(payload.trades);
    renderDecisions(payload.decisions);
    renderRisk(payload.risk);
    renderAgent(payload.agent);
    $("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    setConn(true);
  }

  function setConn(ok) {
    const dot = $("conn-dot");
    const label = $("conn-label");
    dot.className = `dot ${ok ? "dot-on" : "dot-off"}`;
    label.textContent = ok ? "System Online" : "Reconnecting…";
  }

  // ── Live updates: SSE with polling fallback ───────────────────────────

  function startPolling() {
    const tick = async () => {
      try {
        const [portfolio, pnl, positions, trades, decisions, risk, agent] = await Promise.all([
          fetch("/api/portfolio").then((r) => r.json()),
          fetch("/api/pnl").then((r) => r.json()),
          fetch("/api/positions").then((r) => r.json()),
          fetch("/api/trades").then((r) => r.json()),
          fetch("/api/decisions").then((r) => r.json()),
          fetch("/api/risk").then((r) => r.json()),
          fetch("/api/agent/status").then((r) => r.json()),
        ]);
        renderAll({ portfolio, pnl, positions, trades, decisions, risk, agent });
      } catch (e) {
        setConn(false);
      }
    };
    tick();
    setInterval(tick, 5000);
  }

  function startStream() {
    if (!window.EventSource) {
      startPolling();
      return;
    }
    const es = new EventSource("/api/stream");
    es.onmessage = (evt) => {
      try {
        renderAll(JSON.parse(evt.data));
      } catch (e) {
        console.error("Failed to parse stream payload", e);
      }
    };
    es.onerror = () => {
      setConn(false);
      // EventSource auto-reconnects; nothing else to do here.
    };
  }

  // ── Mobile sidebar toggle ──────────────────────────────────────────

  $("menuBtn")?.addEventListener("click", () => {
    $("sidebar").classList.toggle("open");
  });
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => $("sidebar").classList.remove("open"));
  });

  startStream();
})();
