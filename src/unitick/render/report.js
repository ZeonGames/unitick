/*
 * unitick report renderer.
 * Shared by the single-file HTML report and the web app: mountReport(element, payload)
 * draws a whole match report into element and returns a function that removes it.
 */

const BODY = `<div class="topline"><span class="tl-l"><a id="backLink" class="back" hidden>‹ All matches</a><span class="wordmark">uni<i>tick</i></span></span><span id="srcline"></span></div>
<header class="bug" id="hdr"></header>
<div class="rounds" id="rstrip"></div>
<div class="legend" id="legend"></div>
<nav id="tabs">
  <button data-t="overview" class="on"><span>Scoreboard</span></button>
  <button data-t="aim"><span>Aim</span></button>
  <button data-t="deaths"><span>Deaths</span></button>
  <button data-t="exec"><span>Executes</span></button>
  <button data-t="replay"><span>Replay</span></button>
</nav>
<main>
  <section id="overview" class="on"></section>
  <section id="aim"></section>
  <section id="deaths"></section>
  <section id="exec"></section>
  <section id="replay">
    <div class="replay" id="replayGrid">
      <div class="vidcol">
        <div class="vbox" id="vbox"></div>
        <div class="vbar">
          <select id="vSel" title="video"></select>
          <button id="vSync" title="make the video's current moment match the map's current moment">Sync here</button>
          <button id="vMinus" title="video 0.5 s earlier">-0.5s</button>
          <button id="vPlus" title="video 0.5 s later">+0.5s</button>
          <button id="vLock" class="on" title="map follows the video while it plays">Lock</button>
          <button id="vExport" title="download video.json with your sync">Export sync</button>
          <a id="vOpen" target="_blank" rel="noopener">Open video</a>
        </div>
        <div class="vstat" id="vStat"></div>
      </div>
      <div class="roster" id="rosA"></div>
      <div>
        <div class="stage"><canvas id="cv" width="1024" height="1024"></canvas>
          <div class="hud"><div id="hudR"></div><div class="rt" id="hudT"></div></div>
          <div class="feed" id="feed"></div>
        </div>
        <div class="timeline" id="tl"><div class="fill" id="tlf"></div><div class="head" id="tlh"></div></div>
        <canvas class="cstrip" id="cStrip" width="1200" height="44" hidden title="Map control over the round: share of the walkable map each team can reach first"></canvas>
        <div class="cread" id="cRead" hidden></div>
        <div class="ctrl">
          <button id="bBackD" class="backchip" title="back to the death you came from">‹ Back to deaths</button>
          <button id="bPrev" title="previous round [">‹ Round</button>
          <button id="bPlay">▶ Play</button>
          <button id="bNext" title="next round ]">Round ›</button>
          <select id="selR"></select>
          <span class="sp"></span>
          <button data-s="1">1×</button><button data-s="2" class="on">2×</button><button data-s="4">4×</button><button data-s="8">8×</button>
          <button id="bTrail" class="on">Trails</button><button id="bNames" class="on">Names</button><button id="bUtil" class="on">Utility</button><button id="bCtl" hidden title="map control overlay [C]">Control: off</button><button id="b3d" hidden title="3D view with bullet tracers [V]">3D</button><button id="bAll" hidden title="show every shot of the round">All shots</button>
        </div>
        <div class="keys"><kbd>Space</kbd> play &nbsp; <kbd>←</kbd><kbd>→</kbd> 5 seconds &nbsp; <kbd>[</kbd><kbd>]</kbd> round &nbsp; <kbd>T</kbd> trails &nbsp; <kbd>N</kbd> names &nbsp; <kbd>C</kbd> map control &nbsp; <kbd>V</kbd> 3D &nbsp; scroll to zoom, drag to pan, double-click to reset</div>
      </div>
      <div class="roster" id="rosB"></div>
    </div>
  </section>
</main>
<footer id="foot"></footer>`;

export function mountReport(root, D, opts = {}) {
  const ac = new AbortController();
  let alive = true;
  const on = (target, type, fn, o = {}) => target.addEventListener(type, fn, {...o, signal: ac.signal});
  const raf = fn => { if (alive) requestAnimationFrame(t => { if (alive) fn(t); }); };
  root.classList.add("ut-report");
  root.classList.toggle("embedded", !!opts.embedded);
  root.innerHTML = BODY;
  const $ = s => root.querySelector(s);
  let V = null;
  const el = (t, a = {}, h = "") => { const e = document.createElement(t); for (const k in a) e.setAttribute(k, a[k]); e.innerHTML = h; return e; };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const SIDE = {attack: "var(--atk)", defense: "var(--def)"};
  const SIDEHEX = ["#FF6B3D", "#33C3DB"];
  const ICON = {
    bomb: '<svg viewBox="0 0 16 16"><path d="M8 1l1.6 4.2L14 4 11 8l3 4-4.4-1.2L8 15l-1.6-4.2L2 12l3-4-3-4 4.4 1.2z"/></svg>',
    defuse: '<svg viewBox="0 0 16 16"><path d="M3 2l5 5 5-5 1 1-5 5 5 5-1 1-5-5-5 5-1-1 5-5-5-5z"/></svg>',
    kill: '<svg viewBox="0 0 16 16"><path d="M8 1a6 6 0 0 0-4 10.5V14h2v-1.5h1V14h2v-1.5h1V14h2v-2.5A6 6 0 0 0 8 1zM5.5 6.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3zm5 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z"/></svg>',
    time: '<svg viewBox="0 0 16 16"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm.8 3v3.7l2.6 2.6-1.1 1.1L7.2 8.3V4z"/></svg>'};
  const winIcon = why => /defus/i.test(why || "") && !/defusedbomb/i.test(why || "") ? "defuse" : /saved|time/i.test(why || "") ? "time" : /bomb|target|explod|deton/i.test(why || "") ? "bomb" : "kill";
  const GUN = {usp_silencer: "USP-S", m4a1_silencer: "M4A1-S", m4a1: "M4A4", ak47: "AK-47", awp: "AWP", deagle: "Deagle", glock: "Glock", hkp2000: "P2000",
    p250: "P250", tec9: "Tec-9", fiveseven: "Five-SeveN", cz75a: "CZ75", elite: "Dualies", revolver: "R8", galilar: "Galil", famas: "FAMAS", sg556: "SG 553",
    aug: "AUG", ssg08: "Scout", mac10: "MAC-10", mp9: "MP9", mp7: "MP7", mp5sd: "MP5", ump45: "UMP", p90: "P90", bizon: "Bizon", xm1014: "XM1014",
    nova: "Nova", mag7: "MAG-7", sawedoff: "Sawed-Off", negev: "Negev", m249: "M249", g3sg1: "G3SG1", scar20: "SCAR-20", hegrenade: "HE", inferno: "Molotov",
    molotov: "Molotov", incgrenade: "Incendiary", knife: "Knife", taser: "Zeus", world: "World"};
  const gun = w => { const k = String(w || "").replace(/^weapon_/, "").toLowerCase(); return GUN[k] || (k.startsWith("knife") ? "Knife" : k.replace(/_/g, " ")); };
  const NA = '<span style="color:var(--faint)">n/a</span>';
  const fmt = (v, d = 1) => v == null || Number.isNaN(v) ? NA : (typeof v === "number" ? v.toFixed(d) : v);
  const P = D.players, NP = P.length;

  // ---------------------------------------------------------------- header
  (function header() {
    const [A, B] = D.teams;
    const col = t => SIDEHEX[t.start_side === "attack" ? 0 : 1];
    const mapName = D.meta.map_display || (D.meta.map_name || "").replace(/^de_/, "").replace(/_/g, " ");
    const game = {cs2: "Counter-Strike 2", valorant: "VALORANT", r6: "Rainbow Six Siege", synthetic: "Synthetic"}[D.meta.game] || D.meta.game;
    const i = D.info || {};
    const started = s => s === "attack" ? "Started on attack" : "Started on defense";
    $("#hdr").innerHTML = `
      <div class="slab l" style="--c:${col(A)}"><span class="tn">${esc(A.name)}</span><span class="ts">${started(A.start_side)}</span></div>
      <div class="core"><span class="sc ${A.score > B.score ? "win" : ""}">${A.score}</span>
        <span class="mid"><b>${esc(mapName)}</b><small>${esc(i.event || game)}</small></span>
        <span class="sc ${B.score > A.score ? "win" : ""}">${B.score}</span></div>
      <div class="slab r" style="--c:${col(B)}"><span class="tn">${esc(B.name)}</span><span class="ts">${started(B.start_side)}</span></div>`;
    // Round history: coloured by the TEAM that won it, icon = how.
    const teamOf = r => [A, B].find(t => t.side_by_round[r.round_num] === r.winner_side);
    const half = D.round_results.length >= 24 ? 12 : Math.ceil(D.round_results.length / 2);
    const icons = new Set();
    $("#rstrip").innerHTML = D.round_results.map((r, k) => {
      const t = teamOf(r), ic = winIcon(r.reason); icons.add(ic);
      return `<button class="rc ${k === half && D.meta.game !== "r6" ? "half" : ""}" style="--c:${t ? col(t) : "#567"}" data-r="${r.round_num}" title="Round ${r.round_num}: ${esc(t ? t.name : r.winner_side)} won (${esc(r.reason || "")}). Click to watch.">${r.round_num}${ICON[ic]}</button>`;
    }).join("");
    const words = {bomb: "Bomb", defuse: "Defuse", kill: "Elimination", time: "Time"};
    $("#legend").innerHTML = [...icons].map(ic => `<span>${ICON[ic]}${words[ic]}</span>`).join("");
    $("#rstrip").querySelectorAll(".rc").forEach(b => b.onclick = () => { root.querySelector('#tabs button[data-t="replay"]').click(); R.load(+b.dataset.r); });
    const where = [i.stage, i.date].filter(Boolean).join(", ");
    if (D.back) { const b = $("#backLink"); b.href = D.back; b.hidden = false; }
    $("#srcline").innerHTML = (where ? esc(where) : esc(game)) + (i.hltv_match ? `&nbsp;&nbsp;<a href="https://www.hltv.org/matches/${i.hltv_match}/x" target="_blank" rel="noopener">Match page on HLTV</a>` : "");
    $("#foot").textContent = `Made with unitick ${D.version} on ${D.generated}. ${D.summary.rounds} rounds, ${D.summary.n_kills} kills, ${D.summary.n_frames.toLocaleString()} player positions.`;
  })();

  // Hide tabs this source has no data for (Siege replays carry no positions).
  { const has = {aim: D.crosshair.length > 0, deaths: !!(D.deaths && D.deaths.deaths.length), exec: D.executes.length > 0, replay: D.summary.n_frames > 0};
    for (const [t, ok] of Object.entries(has)) if (!ok) root.querySelector(`#tabs button[data-t="${t}"]`).hidden = true;
    if (!has.replay) root.querySelectorAll(".rc").forEach(b => { b.style.cursor = "default"; b.onclick = null; b.title = b.title.replace(" Click to watch.", ""); }); }
  root.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
    root.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b));
    root.querySelectorAll("main section").forEach(s => s.classList.toggle("on", s.id === b.dataset.t));
    if (b.dataset.t === "replay") R.draw();
  });

  // ---------------------------------------------------------------- tables
  function table(rows, cols, opts = {}) {
    if (!rows.length) return `<div class="empty">no data</div>`;
    const id = "t" + Math.random().toString(36).slice(2);
    const th = cols.map((c, i) => `<th data-i="${i}" class="${c.num ? "" : "txt"}" title="${esc(c.tip || "")}">${esc(c.h)}</th>`).join("");
    const body = rows.map(r => `<tr>${cols.map(c => `<td class="${c.num ? "num" : "txt"}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join("")}</tr>`).join("");
    setTimeout(() => {
      const t = document.getElementById(id); if (!t) return;
      t.querySelectorAll("th").forEach(h => h.onclick = () => {
        const c = cols[+h.dataset.i], dir = h.dataset.d === "1" ? -1 : 1; h.dataset.d = dir === 1 ? "1" : "0";
        const v = r => c.sort ? c.sort(r) : r[c.k];
        rows.sort((a, b) => (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : 0) * dir);
        t.outerHTML = table(rows, cols).replace(/id="t[^"]+"/, `id="${id}"`);
      });
    });
    return `<table id="${id}"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
  }
  const sideOf = pid => { const p = P.find(x => x.player_id === pid); return p ? p.first_side : null; };
  const teamColorOf = pid => { const t = D.teams.find(t => t.player_ids.includes(pid)); return t ? SIDEHEX[t.start_side === "attack" ? 0 : 1] : "#567"; };
  const nameCell = (name, side, pid) => `<span class="pl" style="--c:${pid ? teamColorOf(pid) : (SIDE[side] || "#567")}"><i></i>${esc(name)}</span>`;

  // ---------------------------------------------------------------- overview
  (function overview() {
    const sec = $("#overview");
    const sb = D.scoreboard.map(r => ({...r}));
    const hasAdr = sb.some(r => r.adr != null);
    const key = r => hasAdr ? r.adr : r.kills - r.deaths;
    const mvp = sb.length ? sb.reduce((a, b) => key(b) > key(a) ? b : a).player_id : null;
    const cols = [
      {h: "Player", k: "player_name", f: r => nameCell(r.player_name, r.first_side, r.player_id) + (r.player_id === mvp ? '<span class="mvp" title="highest ADR in the match">MVP</span>' : "")},
      {h: "K", k: "kills", num: 1}, {h: "D", k: "deaths", num: 1}, {h: "A", k: "assists", num: 1, f: r => r.assists == null ? NA : r.assists},
      {h: "+/-", k: "diff", num: 1, f: r => `<span style="color:${r.diff >= 0 ? "var(--good)" : "var(--bad)"}">${r.diff > 0 ? "+" : ""}${r.diff}</span>`},
      ...(hasAdr ? [{h: "ADR", k: "adr", num: 1, f: r => fmt(r.adr)}] : []),
      {h: "KAST", k: "kast", num: 1, f: r => fmt(r.kast, 0) + "%", tip: "rounds with a kill, assist, survival or traded death"},
      {h: "HS", k: "hs_pct", num: 1, f: r => r.hs_pct == null ? NA : fmt(r.hs_pct, 0) + "%"},
      {h: "Entry", k: "opening_kills", num: 1, f: r => `${r.opening_kills}-${r.opening_deaths}`, tip: "opening duels won-lost"},
      {h: "Trades", k: "trade_kills", num: 1, tip: "kills that avenged a teammate"},
    ];
    sec.innerHTML = `<div class="teams">${D.teams.map(t => {
      const rows = sb.filter(r => t.player_ids.includes(r.player_id));
      return `<div class="tcard"><div class="thead" style="--c:${SIDEHEX[t.start_side === "attack" ? 0 : 1]}"><b>${esc(t.name)}</b><span>${t.score}</span></div>${table(rows, cols)}</div>`;
    }).join("")}</div>`;
  })();

  // ---------------------------------------------------------------- crosshair
  (function aim() {
    const sec = $("#aim");
    const L = D.crosshair;
    if (!L.length) { sec.innerHTML = `<div class="empty">No engagements with aim data.</div>`; return; }
    const pts = D.crosshair_points;
    const med = a => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : NaN; };
    const errs = pts.map(p => p[1]);
    const modeTxt = D.crosshair_mode === "line-of-sight"
      ? "at the first moment the enemy came into view (checked against the map's walls)"
      : D.meta.game === "valorant" ? "at the start of the player's final flick onto the enemy" : "0.2 s before the first shot";
    sec.innerHTML = `
      <h2>Crosshair placement</h2>
      <div class="sub">How far each player's crosshair was from the enemy's head, ${modeTxt}. The score is 100 when the crosshair was already on the head, 50 at 5° away and 25 at 10°.</div>
      <div class="kpi">
        <div><div class="v">${esc(L[0].player)}</div><div class="l">best placement, scoring ${fmt(L[0].cps)}</div></div>
        <div><div class="v">${fmt(med(errs), 1)}°</div><div class="l">typical distance from the head</div></div>
        <div><div class="v">${fmt(100 * errs.filter(e => e < 5).length / errs.length, 0)}%</div><div class="l">of fights started within 5°</div></div>
        <div><div class="v">${pts.length}</div><div class="l">fights measured</div></div>
      </div>
      <div class="grid2">
        <div class="card">${table(L.map(r => ({...r})), [
          {h: "Player", k: "player", f: r => nameCell(r.player, sideOf(r.player_id), r.player_id)},
          {h: "Score", k: "cps", num: 1, f: r => `${fmt(r.cps)}<span class="bar" style="--c:${teamColorOf(r.player_id)}"><i style="width:${r.cps}%"></i></span>`},
          {h: "Fights", k: "engagements", num: 1},
          {h: "Typical", k: "median_err_deg", num: 1, f: r => fmt(r.median_err_deg, 1) + "°", tip: "median angle from the head"},
          {h: "Sideways", k: "median_horizontal_deg", num: 1, f: r => fmt(r.median_horizontal_deg, 1) + "°", tip: "median left/right error"},
          {h: "Height", k: "median_vertical_deg", num: 1, f: r => fmt(r.median_vertical_deg, 1) + "°", tip: "median up/down error: head-height placement"},
          {h: "Within 5°", k: "pct_under_5deg", num: 1, f: r => fmt(r.pct_under_5deg, 0) + "%"},
          {h: "Reaction", k: "median_reaction_s", num: 1, f: r => r.median_reaction_s == null ? NA : fmt(r.median_reaction_s * 1000, 0) + " ms", tip: "from first sight to first shot"},
          {h: "Every fight", k: "cps", f: r => D.crosshair_strips[r.player_id] || "", tip: "each dot is one fight, 0° (left) to 20° (right)"},
        ])}</div>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <b style="font:800 22px var(--display);text-transform:uppercase">Where the crosshair was</b>
            <select class="ps" id="aimSel"><option value="-1">Everyone</option>${L.map(r => `<option value="${P.findIndex(p => p.player_id === r.player_id)}">${esc(r.player)}</option>`).join("")}</select>
          </div>
          <canvas class="scatter" id="aimCv" width="900" height="900"></canvas>
          <div class="sub" style="margin-top:10px">Each dot is one fight, drawn where the crosshair sat relative to the enemy's head (centre). Dots above the centre were aimed too high. Big dots are fights the player won.</div>
        </div>
      </div>`;
    const cv = $("#aimCv"), g = cv.getContext("2d");
    function draw(sel) {
      const W = cv.width, c = W / 2, lim = 15, k = (W / 2 - 30) / lim;
      g.clearRect(0, 0, W, W);
      g.strokeStyle = "#241d33"; g.lineWidth = 2;
      g.beginPath(); g.moveTo(c, 20); g.lineTo(c, W - 20); g.moveTo(20, c); g.lineTo(W - 20, c); g.stroke();
      [[2.5, "#5BE3A866"], [5, "#F5C45166"], [10, "#FF5C7A66"]].forEach(([r, col]) => { g.strokeStyle = col; g.beginPath(); g.arc(c, c, r * k, 0, 7); g.stroke();
        g.fillStyle = "#b7adc8"; g.font = "600 24px 'Barlow Condensed',sans-serif"; g.fillText(r + "°", c + r * k * .71 + 6, c - r * k * .71 - 6); });
      // head silhouette at the origin
      g.fillStyle = "#f5f1ff22"; g.beginPath(); g.arc(c, c, 0.9 * k, 0, 7); g.fill();
      for (const p of pts) {
        if (sel >= 0 && p[0] !== sel) continue;
        const x = c - Math.max(-lim, Math.min(lim, p[2])) * k, y = c + Math.max(-lim, Math.min(lim, p[3])) * k;
        g.fillStyle = p[1] < 5 ? "#5BE3A8" : p[1] < 10 ? "#F5C451" : "#FF5C7A"; g.globalAlpha = sel >= 0 ? .9 : .55;
        g.beginPath(); g.arc(x, y, p[8] ? 7 : 5, 0, 7); g.fill();
      }
      g.globalAlpha = 1;
    }
    draw(-1);
    $("#aimSel").onchange = e => draw(+e.target.value);
  })();

  // ---------------------------------------------------------------- deaths
  (function deaths() {
    const sec = $("#deaths"), X = D.deaths;
    if (!X || !X.deaths.length) { sec.innerHTML = `<div class="empty">No deaths with position data.</div>`; return; }
    const CC = {no_sight: "#8b819f", off_angle: "#FF5C7A", crossfire: "#B48CFF", late: "#FFB347", duel: "#5BC0EB", unknown: "#7d7391"};
    const SHORT = {no_sight: "No line of sight", off_angle: "Looking away", crossfire: "Crossfire", late: "Did not shoot", duel: "Lost the duel", unknown: "Unclear"};
    const TR = D.meta.tick_rate || 64;
    const radar = new Image(); if (D.radar) radar.src = D.radar;
    const sil = new Image(); if (D.silhouette) sil.src = D.silhouette;
    const S = D.transform.size;
    let who = "-1", cause = null, cur = 0;
    const rounds = Object.fromEntries(D.rounds.map(r => [r.n, r]));
    const clockOf = d => { const r = rounds[d.round_num]; if (!r) return ""; const s = Math.max(0, Math.floor((d.tick - r.live_tick) / TR)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
    const pname = i => P[i]?.player_name ?? "?";
    const pcol = i => i >= 0 ? teamColorOf(P[i].player_id) : "#567";

    // per-player profile: how each player tends to die
    const prof = X.players.filter(p => p.deaths >= 3).map(p => ({...p, name: pname(p.pi)}));
    const stack = p => `<span class="pstack" title="${Object.keys(SHORT).filter(c => p[c]).map(c => `${SHORT[c]}: ${p[c]}`).join(", ")}">${
      ["off_angle", "crossfire", "late", "duel", "no_sight"].map(c => p[c] ? `<i style="width:${100 * p[c] / p.deaths}%;background:${CC[c]}"></i>` : "").join("")}</span>`;
    const lib = X.library, WI = X.whatif;
    const PLAY = {held: "Hold the spot", pushed: "Push toward them", "fell back": "Fall back"};
    const DID = {held: "held the spot", pushed: "pushed toward them", "fell back": "fell back"};
    const ING = {held: "holding the spot", pushed: "pushing toward them", "fell back": "falling back"};
    const pct = v => v == null ? "" : `${Math.round(100 * v)}%`;
    const wiTxt = WI && WI.rated ? ` In <b style="color:var(--text)">${WI.better} of ${WI.rated}</b> deaths with enough ${lib && lib.matches.some(m => m.pro) ? "pro" : "comparable"} data, another play had clearly better odds of surviving.` : "";
    const libTxt = lib ? `Each death is matched against ${lib.matches.length} other ${esc(D.meta.map_display || (D.meta.map_name || "").replace(/^de_/, ""))} matches${lib.matches.some(m => m.pro) ? `, ${lib.matches.filter(m => m.pro).length} of them professional` : ""}.` : "Add a library of other matches on this map to see what other players did in the same spot.";
    sec.innerHTML = `
      <h2>Why you died</h2>
      <div class="sub">Every death rebuilt from the replay${X.used_los ? ", with line of sight checked against the map's walls" : ""}. Pick one to see what the killer saw, what you were doing, your odds of surviving with every other play, and what players in professional matches did when they stood in the same spot against the same setup. ${libTxt}${wiTxt}</div>
      <div class="card" style="margin-bottom:18px">${table(prof, [
        {h: "Player", k: "name", f: r => nameCell(r.name, null, P[r.pi]?.player_id)},
        {h: "Deaths", k: "deaths", num: 1},
        {h: "Looking away", k: "off_angle", num: 1, tip: "crosshair more than 40° off the killer when he first had sight"},
        {h: "Crossfire", k: "crossfire", num: 1, tip: "two or more enemies could see you in the last second"},
        {h: "Did not shoot", k: "late", num: 1, tip: "looking the right way, but never fired back"},
        {h: "Lost the duel", k: "duel", num: 1, tip: "both aiming, both shooting, the killer won"},
        {h: "No sight", k: "no_sight", num: 1, tip: "wallbangs, smokes and utility"},
        ...(X.used_los ? [{h: "Not tradeable", k: "untradeable", num: 1, tip: "no teammate could see the killer within 2 s"}] : []),
        ...(WI ? [{h: "Better play", k: "better_play", num: 1, f: r => r.better_play == null ? NA : r.better_play, tip: "deaths where another play had clearly better odds of surviving"}] : []),
        {h: "Mix", k: "deaths", f: stack},
      ])}</div>
      <div class="dtools">
        <select class="ps" id="dWho"><option value="-1">Everyone</option>${[...new Set(X.deaths.map(d => d.victim))].filter(i => i >= 0).sort((a, b) => pname(a).localeCompare(pname(b))).map(i => `<option value="${i}">${esc(pname(i))}</option>`).join("")}</select>
        <span id="dChips"></span>
      </div>
      <div class="dwrap"><div class="dlist" id="dList"></div><div id="dDetail"></div></div>`;

    function filtered() { return X.deaths.map((d, i) => ({...d, i})).filter(d => (who === "-1" || d.victim === +who) && (!cause || d.cause === cause)); }
    function chips() {
      const base = X.deaths.filter(d => who === "-1" || d.victim === +who), n = {};
      base.forEach(d => n[d.cause] = (n[d.cause] || 0) + 1);
      $("#dChips").innerHTML = Object.keys(SHORT).filter(c => n[c]).map(c => `<button class="cz ${cause === c ? "on" : ""}" data-c="${c}" style="--c:${CC[c]}"><i></i>${SHORT[c]} <b>${n[c]}</b></button>`).join(" ");
      $("#dChips").querySelectorAll(".cz").forEach(b => b.onclick = () => { cause = cause === b.dataset.c ? null : b.dataset.c; render(); });
    }
    function list() {
      const L = filtered(), box = $("#dList");
      if (!L.length) { box.innerHTML = `<div class="empty">No deaths match.</div>`; return; }
      if (!L.some(d => d.i === cur)) cur = L[0].i;
      box.innerHTML = L.map(d => `<button class="drow ${d.i === cur ? "on" : ""}" data-i="${d.i}" style="--c:${CC[d.cause]}">
        <span class="rn">${d.round_num}</span>
        <span class="who"><span style="color:${pcol(d.victim)}">${esc(d.victim_name)}</span> <small>by</small> <span style="color:${pcol(d.killer)}">${esc(d.killer_name)}</span><br><small>${d.weapon ? esc(gun(d.weapon)) + ", " : ""}${d.headshot ? "headshot, " : ""}${clockOf(d)}</small>${d.whatif && d.whatif.verdict === "better" ? `<span class="wi" title="${esc(PLAY[d.whatif.best])} had ${pct(d.whatif.gain)} better odds">${esc(PLAY[d.whatif.best].toLowerCase())} +${Math.round(100 * d.whatif.gain)}</span>` : ""}</span>
        <span class="tag">${SHORT[d.cause]}</span></button>`).join("");
      box.querySelectorAll(".drow").forEach(b => b.onclick = () => { cur = +b.dataset.i; list(); detail(); });
    }

    // draw a scene cropped around a point; scene rows = [px, py, heading, side, pidx, isMe]
    function scene(cv, rows, opt = {}) {
      const g = cv.getContext("2d"), W = cv.width;
      const me = rows.find(r => r[5]) || rows[0];
      const cx = opt.cx ?? me[0], cy = opt.cy ?? me[1], span = opt.span ?? 340;
      const sc = W / span, x0 = Math.max(0, Math.min(S - span, cx - span / 2)), y0 = Math.max(0, Math.min(S - span, cy - span / 2));
      g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = "#09060f"; g.fillRect(0, 0, W, W);
      g.setTransform(sc, 0, 0, sc, -x0 * sc, -y0 * sc);
      const k = 1 / sc * (W / 520);
      if (radar.complete && radar.naturalWidth) { g.globalAlpha = .9; g.drawImage(radar, 0, 0, S, S); g.globalAlpha = 1; }
      else if (sil.complete && sil.naturalWidth) g.drawImage(sil, 0, 0, S, S);
      if (opt.line) { const [a, b] = opt.line; g.strokeStyle = "#ffffffcc"; g.setLineDash([6 * k, 5 * k]); g.lineWidth = 2 * k;
        g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); g.setLineDash([]); }
      if (opt.to) { const [a, b] = [[me[0], me[1]], opt.to]; g.strokeStyle = "#5BE3A8"; g.lineWidth = 3 * k;
        g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]); g.fillStyle = "#5BE3A8"; g.beginPath();
        g.moveTo(b[0], b[1]); g.lineTo(b[0] - 11 * k * Math.cos(ang - .5), b[1] - 11 * k * Math.sin(ang - .5)); g.lineTo(b[0] - 11 * k * Math.cos(ang + .5), b[1] - 11 * k * Math.sin(ang + .5)); g.fill(); }
      for (const r of rows) {
        const [x, y, h, sd, pi, isMe] = r, c = SIDEHEX[sd], rad = (isMe ? 9 : 7) * k;
        if (h != null) { const yr = h * Math.PI / 180; g.fillStyle = c + "40"; g.beginPath(); g.moveTo(x, y); g.arc(x, y, 40 * k, yr - .4, yr + .4); g.closePath(); g.fill(); }
        g.fillStyle = c; g.strokeStyle = isMe ? "#fff" : "#07050c"; g.lineWidth = (isMe ? 3 : 2) * k;
        g.beginPath(); g.arc(x, y, rad, 0, 7); g.fill(); g.stroke();
        const label = opt.names !== false && pi >= 0 ? pname(pi) : isMe ? (opt.meLabel || "") : "";
        if (label) { g.font = `600 ${15 * k}px 'Barlow Condensed',sans-serif`; g.fillStyle = "#fff"; g.strokeStyle = "#000c"; g.lineWidth = 3 * k;
          g.strokeText(label, x + 12 * k, y - 10 * k); g.fillText(label, x + 12 * k, y - 10 * k); }
      }
      if (opt.x) { const [x, y] = opt.x, s = 7 * k; g.strokeStyle = "#fff"; g.lineWidth = 3 * k;
        g.beginPath(); g.moveTo(x - s, y - s); g.lineTo(x + s, y + s); g.moveTo(x - s, y + s); g.lineTo(x + s, y - s); g.stroke(); }
    }

    const sentence = (n, one, many) => n === 1 ? one : many.replace("#", n);
    function detail() {
      const d = X.deaths[cur], box = $("#dDetail"); if (!d) { box.innerHTML = ""; return; }
      const kn = esc(d.killer_name), vn = esc(d.victim_name);
      const facts = [];
      if (d.exposed_s != null) facts.push([`${fmt(d.exposed_s, 2)} s`, `${kn} had ${vn} in sight before the kill`, d.exposed_s < .35 ? "bad" : ""]);
      if (d.facing_err != null) facts.push([`${fmt(d.facing_err, 0)}°`, X.used_los ? `between ${vn}'s crosshair and ${kn} when ${kn} first had sight` : `between ${vn}'s crosshair and ${kn}, 0.3 s before the kill`, d.facing_err > 40 ? "bad" : d.facing_err < 8 ? "ok" : ""]);
      if (d.killer_err != null) facts.push([`${fmt(d.killer_err, 0)}°`, `between ${kn}'s crosshair and ${vn}: ${d.killer_err < 5 ? "already aimed there" : "had to adjust"}`, ""]);
      if (d.shots_back != null) facts.push([`${d.shots_back}`, sentence(d.shots_back, "shot fired back", "shots fired back"), d.shots_back === 0 ? "bad" : ""]);
      if (d.crossfire != null) facts.push([`${d.crossfire}`, sentence(d.crossfire, "enemy could see you in the last second", "enemies could see you in the last second"), d.crossfire >= 2 ? "bad" : ""]);
      if (d.tradeable != null) facts.push([d.tradeable ? "Yes" : "No", d.tradeable ? "a teammate had sight of the killer within 2 s" : "no teammate could see the killer within 2 s", d.tradeable ? "ok" : "bad"]);
      const w = d.whatif;
      let wif = "";
      if (w) {
        const pool = w.pool === "pro" ? "pros" : "players";
        const byMove = Object.fromEntries(w.options.map(x => [x.move, x])), mine = byMove[w.you], top = byMove[w.best];
        const lead = !top ? `Too few ${pool} were in this exact spot to compare plays.`
          : w.verdict === "better" ? `${esc(d.victim_name)} ${DID[w.you]}. <b>${PLAY[w.best]}</b> had clearly better odds here: ${pct(top.rate)} to survive, against ${pct(mine.rate)} for ${ING[w.you]}.`
          : w.verdict === "best" ? `${esc(d.victim_name)} ${DID[w.you]}, and that was the play with the best odds here: ${pct(mine.rate)} to survive. This death came down to the fight, not the decision.`
          : w.verdict === "close" ? `${esc(d.victim_name)} ${DID[w.you]}. No play was clearly better here: ${pct(mine.rate)} to survive for ${ING[w.you]}, ${pct(top.rate)} for ${ING[w.best]}.`
          : w.verdict === "rare" ? `${esc(d.victim_name)} ${DID[w.you]}, which few ${pool} did here, so that play cannot be rated. <b>${PLAY[w.best]}</b> had the best odds: ${pct(top.rate)} to survive.`
          : `<b>${PLAY[w.best]}</b> had the best odds here: ${pct(top.rate)} to survive.`;
        wif = `<div class="whatif">
          <h3>What if</h3>
          <div class="lead">${lead}</div>
          <div class="wopts">${w.options.map(x => {
            const cls = [x.move === w.best && x.enough ? "best" : "", x.move === w.you ? "you" : "", x.enough ? "" : "thin"].join(" ");
            const tags = (x.move === w.you ? `<em>${esc(d.victim_name)}</em>` : "") + (x.move === w.best && x.enough ? `<em class="b">best odds</em>` : "");
            const bar = x.enough ? `<i class="pt" style="width:${100 * x.rate}%"></i><i class="ci" title="likely range ${pct(x.lo)} to ${pct(x.hi)}" style="left:${100 * x.lo}%;width:${100 * (x.hi - x.lo)}%"></i><span class="pv">${pct(x.rate)}</span>` : "";
            const one = pool.replace(/s$/, "");
            const note = x.n === 0 ? `no ${pool} did this` : x.enough ? `${x.n} ${pool} did this${x.kill_rate ? `, ${pct(x.kill_rate)} got a kill` : ""}` : `only ${x.n} ${x.n === 1 ? one : pool}, too few to rate`;
            return `<div class="wo ${cls}"><div class="wl">${PLAY[x.move]}${tags}</div><div class="wbar">${bar}</div><div class="wn">${note}</div></div>`;
          }).join("")}</div>
          <div class="mcap">Odds of being alive ${fmt(w.horizon_s, 0)} seconds later, from the ${w.n} ${w.pool === "pro" ? "professional" : "most similar"} moments closest to this one (same spot, setup, time and bomb state, one per round). Closer matches count more, and a play few ${pool} chose leans toward the spot's overall odds. The light band is the likely range. This is what happened to ${pool} who made each choice, not a guarantee.</div></div>`;
      } else if (lib) wif = `<div class="whatif"><h3>What if</h3><div class="sub">Too few similar moments in the library to compare plays here.</div></div>`;
      else wif = `<div class="whatif"><h3>What if</h3><div class="sub">${D.meta.game === "cs2"
        ? "The what-if compares this moment with professional matches on the same map, and the library has none for this map yet."
        : "The what-if compares this moment with professional matches on the same map. It covers CS2 today; VALORANT and Siege X get it as pro replays are added to the library."}</div></div>`;
      const o = d.others;
      let spot = "";
      if (o) {
        const pool = o.pro ? "professional players" : "players in other matches";
        const best = [...o.by_move].sort((a, b) => b.survived / b.n - a.survived / a.n || b.n - a.n)[0];
        spot = `<div class="spot">
          <h3>In this spot</h3>
          <div class="sub" style="margin-bottom:0">${o.n} ${pool} stood here, on the ${d.side}, against a similar enemy setup at a similar time. <b style="color:var(--text)">${o.survived} of ${o.n}</b> were still alive five seconds later, and they got ${o.kills} ${o.kills === 1 ? "kill" : "kills"} between them.</div>
          <div class="moves">${o.by_move.map(m => `<div class="mv ${m === best && m.survived ? "best" : ""}"><div class="v">${m.survived} of ${m.n}</div><div class="l">${m.move === "held" ? "held the spot" : m.move === "pushed" ? "pushed toward the enemy" : m.move === "fell back" ? "fell back" : "repositioned"} and lived${m.kills ? `, ${m.kills} ${m.kills === 1 ? "kill" : "kills"}` : ""}</div><div class="meter"><i style="width:${100 * m.survived / m.n}%"></i></div></div>`).join("")}</div>
          <div class="alts">${o.shown.map((a, j) => `<div class="alt"><canvas class="mini" width="520" height="520" id="alt${j}"></canvas>
            <div class="t">${esc(a.player)}: ${a.move}, ${a.died ? '<span style="color:var(--bad)">died</span>' : '<span style="color:var(--good)">survived</span>'}${a.kills ? `, ${a.kills} ${a.kills === 1 ? "kill" : "kills"}` : ""}<small>${esc(a.label)}, round ${a.round}, ${fmt(a.t_live, 0)} s in</small></div></div>`).join("")}</div>
          <div class="mcap">Green arrow: where that player was three seconds later.</div></div>`;
      } else if (lib) spot = `<div class="spot"><div class="sub">No similar situation on this map in the library.</div></div>`;
      box.innerHTML = `<div class="dcard" style="--c:${CC[d.cause]}">
        <div class="dline">Round ${d.round_num}, ${clockOf(d)}: <b style="color:${pcol(d.victim)}">${vn}</b> killed by <b style="color:${pcol(d.killer)}">${kn}</b>${d.weapon ? ` with the ${esc(gun(d.weapon))}` : ""}${d.headshot ? ", headshot" : ""}</div>
        <div class="verdict">${esc(d.cause_text)}</div>
        <div class="dgrid">
          <div><canvas class="mini" width="760" height="760" id="dMap"></canvas>
            <div class="mcap">${d.before ? "1.5 seconds before the death. Dashed line: where the shot came from. Cross: where it happened." : "Where it happened."}</div>
            <div class="thumbs"><button id="dWatch">Watch in replay</button></div></div>
          <dl class="facts">${facts.map(f => `<dt class="${f[2]}">${f[0]}</dt><dd>${f[1]}</dd>`).join("")}</dl>
        </div>${wif}${spot}</div>`;
      const main = $("#dMap");
      const rows = d.before || [[d.at.v[0], d.at.v[1], null, d.side === "attack" ? 0 : 1, d.victim, 1]];
      const mid = d.at.k[0] != null ? [(d.at.v[0] + d.at.k[0]) / 2, (d.at.v[1] + d.at.k[1]) / 2] : d.at.v;
      const dist = d.at.k[0] != null ? Math.hypot(d.at.v[0] - d.at.k[0], d.at.v[1] - d.at.k[1]) : 0;
      const draw = () => {
        scene(main, rows, {cx: mid[0], cy: mid[1], span: Math.max(300, Math.min(700, dist * 1.8)), line: d.at.k[0] != null ? [d.at.k, d.at.v] : null, x: d.at.v});
        if (o) o.shown.forEach((a, j) => scene($("#alt" + j), a.scene, {span: 300, to: a.to, names: false, meLabel: a.player}));
      };
      draw(); if (!radar.complete) radar.onload = draw;
      $("#dWatch").onclick = () => { root.querySelector('#tabs button[data-t="replay"]').click(); R.seekTick(d.round_num, d.tick - Math.round(TR * 3), "deaths"); };
    }
    function render() { chips(); list(); detail(); }
    $("#dWho").onchange = e => { who = e.target.value; cause = null; render(); };
    render();
  })();

  // ---------------------------------------------------------------- executes
  (function exec() {
    const sec = $("#exec");
    const E = D.executes;
    if (!E.length) { sec.innerHTML = `<div class="empty">No objective plants in this match.</div>`; return; }
    const hasSite = v => v != null && v !== "" && v !== "null";
    const siteLabel = v => hasSite(v) ? `${esc(v)} site` : "Plants";
    const bySite = {};
    E.forEach(e => { (bySite[hasSite(e.site) ? e.site : ""] ??= []).push(e); });
    const siteRows = Object.entries(bySite).map(([s, xs]) => ({site: s, n: xs.length, won: xs.filter(x => x.won).length,
      t: xs.reduce((a, x) => a + x.plant_time_s, 0) / xs.length, u: xs.reduce((a, x) => a + x.smokes + x.flashes + x.fires + x.hes, 0) / xs.length}));
    sec.innerHTML = `
      <h2>Site executes</h2>
      <div class="sub">Every round with a plant: what the attackers did in the 20 seconds before it, the grenades they used, the entry fights, and whether they won the round.</div>
      <div class="kpi">${siteRows.map(s => `<div><div class="v">${siteLabel(s.site)}: ${s.won} of ${s.n}</div><div class="l">rounds won after planting. Plant at ${fmt(s.t, 0)} s on average, ${fmt(s.u, 1)} grenades per execute.</div></div>`).join("")}</div>
      <div class="card">${table(E.map(r => ({...r})), [
        {h: "Round", k: "round_num", num: 1}, {h: "Site", k: "site", f: r => hasSite(r.site) ? esc(r.site) : NA}, {h: "Planter", k: "planter"},
        {h: "Plant @s", k: "plant_time_s", num: 1, tip: "seconds after the round went live"},
        {h: "Alive", k: "alive_at_plant", num: 1}, {h: "Smokes", k: "smokes", num: 1}, {h: "Flashes", k: "flashes", num: 1},
        {h: "Molos", k: "fires", num: 1}, {h: "HE", k: "hes", num: 1}, {h: "Entry K", k: "entry_kills", num: 1}, {h: "Entry D", k: "entry_deaths", num: 1},
        {h: "Result", k: "won", f: r => `<span style="color:${r.won ? "var(--good)" : "var(--bad)"}">${r.won ? "won" : "lost"}</span>`},
        {h: "", k: "round_num", f: r => `<a href="#" data-r="${r.round_num}" class="toReplay">Watch</a>`},
      ])}</div>
      <h2>Playbook</h2>
      <div class="sub">Executes that put players in the same spots 15, 10, 5 and 0 seconds before the plant are grouped as one play, whoever played which role. Plays a team repeats show up as one card with several rounds.</div>
      <div class="plays" id="plays"></div>`;
    const wrap = $("#plays");
    D.plays.forEach(p => {
      const card = el("div", {class: "card play"});
      card.innerHTML = `<h3>${p.rounds.length > 1 ? `${hasSite(p.site) ? esc(p.site) + " play" : "Play"}, run ${p.rounds.length} times` : `${hasSite(p.site) ? esc(p.site) + " execute" : "Execute"}, round ${p.rounds[0]}`}</h3>
        <div class="meta">${p.rounds.length > 1 ? "Rounds " + p.rounds.join(", ") : "Not repeated in this match"}</div>
        <div class="sk"></div><div class="thumbs"></div>`;
      const sk = card.querySelector(".sk"), th = card.querySelector(".thumbs");
      const show = i => { sk.innerHTML = p.sketches[i].svg; th.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", j === i)); };
      p.sketches.forEach((s, i) => { const b = el("button", {}, `Round ${s.round}`); b.onclick = () => show(i); th.appendChild(b); });
      const go = el("button", {}, "Watch"); go.onclick = () => { root.querySelector('#tabs button[data-t="replay"]').click(); R.load(p.rounds[0]); }; th.appendChild(go);
      show(0); wrap.appendChild(card);
    });
    sec.querySelectorAll(".toReplay").forEach(a => a.onclick = ev => { ev.preventDefault(); root.querySelector('#tabs button[data-t="replay"]').click(); R.load(+a.dataset.r); });
  })();

  // ---------------------------------------------------------------- replay
  const R = (function () {
    const cv = $("#cv"), g = cv.getContext("2d");
    const S = D.transform.size, TR = D.meta.tick_rate || 64, STEP = D.step, SPS = TR / STEP; // slots per second
    const imgs = {};
    for (const k of ["radar", "silhouette"]) if (D[k]) { const im = new Image(); im.onload = () => draw(); im.src = D[k]; imgs[k] = im; }
    let rd = null, F = null, t = 0, playing = false, speed = 2, last = 0;
    let trails = true, names = true, util = true;
    let zoom = 1, ox = 0, oy = 0;

    const sel = $("#selR");
    D.rounds.forEach(r => sel.appendChild(el("option", {value: r.n}, `Round ${r.n}${r.site ? ", " + r.site + " site" : ""}: ${r.winner} won`)));
    sel.onchange = () => load(+sel.value);

    function decode(b64) { const bin = atob(b64), u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new Int16Array(u8.buffer); }
    function load(n) {
      rd = D.rounds.find(r => r.n === n) || D.rounds[0]; if (!rd) return;
      F = decode(rd.frames); t = Math.max(0, rd.live - SPS * 1); sel.value = rd.n;
      buildTimeline(); roster(); if (C) C.strip(); draw();
      if (!quiet) userSeek();
    }
    let quiet = false;
    function userSeek() { if (V) V.follow(rd, t); }
    function setPos(n, tt) {  // called by the video when the map follows it
      if (!rd || rd.n !== n) { quiet = true; load(n); quiet = false; }
      t = Math.max(0, Math.min(rd.nt - 1, tt)); draw();
    }
    const MISSING = -32768;
    function at(i, p) { const o = (i * NP + p) * 4; return F[o] === MISSING ? null : [F[o] / 8, F[o + 1] / 8, F[o + 2] / 10, F[o + 3]]; }
    function pos(p, tt) {
      const i = Math.floor(tt), f = tt - i, a = at(Math.min(i, rd.nt - 1), p), b = at(Math.min(i + 1, rd.nt - 1), p);
      if (!a) return b; if (!b || b[3] <= 0 && a[3] > 0) return a;
      let dy = ((b[2] - a[2] + 540) % 360) - 180;
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + dy * f, f < .5 ? a[3] : b[3]];
    }
    function lastAlive(p, tt) { for (let i = Math.floor(tt); i >= 0; i--) { const a = at(i, p); if (a && a[3] > 0) return a; } return null; }

    function buildTimeline() {
      const tl = $("#tl"); tl.querySelectorAll(".mk,.live").forEach(x => x.remove());
      const pc = x => (100 * x / (rd.nt - 1)) + "%";
      tl.appendChild(el("div", {class: "live", style: `left:${pc(rd.live)}`}));
      rd.kills.forEach(k => { const m = el("div", {class: "mk", style: `left:${pc(k[0])};background:${k[1] >= 0 ? SIDEHEX[rd.side[k[1]]] : "#888"}`, title: `${P[k[1]]?.player_name ?? "?"} ▸ ${P[k[2]]?.player_name ?? "?"} (click: jump here)`});
        m.onpointerdown = ev => { ev.stopPropagation(); t = Math.max(0, k[0] - SPS * 3); draw(); userSeek(); }; tl.appendChild(m); });
      if (rd.plant != null) tl.appendChild(el("div", {class: "mk", style: `left:${pc(rd.plant)};background:#ff3b3b;height:30px;top:2px;width:4px`, title: "plant"}));
    }
    $("#tl").onpointerdown = e => {
      const r = e.currentTarget.getBoundingClientRect();
      const seek = ev => { t = Math.max(0, Math.min(rd.nt - 1, (ev.clientX - r.left) / r.width * (rd.nt - 1))); draw(); userSeek(); };
      seek(e); const mv = ev => seek(ev); window.addEventListener("pointermove", mv);
      window.addEventListener("pointerup", () => window.removeEventListener("pointermove", mv), {once: true});
    };

    function roster() {
      for (const [elId, s] of [["#rosA", 0], ["#rosB", 1]]) {
        const box = $(elId); box.style.setProperty("--c", SIDEHEX[s]); box.innerHTML = `<h4>${s === 0 ? "Attack" : "Defense"}</h4>`;
        rd.side.forEach((sd, p) => { if (sd !== s) return; box.appendChild(el("div", {class: "p", id: "ro" + p},
          `<span class="e"></span><span class="nm">${esc(P[p].player_name)}</span><span class="hp"><i></i></span>`)); });
      }
    }
    function clock() {
      if (t < rd.live) return "Buy " + Math.ceil((rd.live - t) / SPS);
      const s = Math.floor((t - rd.live) / SPS); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }

    // ---------------------------------------------------------------- map control
    // Walkable grid learned from every position seen on this map; BFS for reach, rays for vision.
    const C = (function () {
      if (!D.control) return null;
      const n = D.control.n, bin = atob(D.control.cells), G = new Uint8Array(n * n);
      for (let i = 0; i < n * n; i++) G[i] = (bin.charCodeAt(i >> 3) >> (7 - (i & 7))) & 1;
      const cs = S / n, q = new Int32Array(n * n), dA = new Int16Array(n * n), dB = new Int16Array(n * n);
      const blk = new Uint8Array(n * n), smk = new Uint8Array(n * n), seenA = new Uint8Array(n * n), seenB = new Uint8Array(n * n);
      const off = document.createElement("canvas"); off.width = off.height = n;
      const og = off.getContext("2d"), img = og.createImageData(n, n);
      const RGB = [[255, 107, 61], [51, 195, 219]];
      function cell(x, y) {
        let cx = Math.floor(x / cs), cy = Math.floor(y / cs);
        if (cx < 0 || cy < 0 || cx >= n || cy >= n) return -1;
        if (G[cy * n + cx]) return cy * n + cx;
        for (let r = 1; r <= 4; r++) for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
          const yy = cy + dy, xx = cx + dx; if (yy >= 0 && xx >= 0 && yy < n && xx < n && G[yy * n + xx]) return yy * n + xx; }
        return -1;
      }
      function masks(tt) {  // burning fire blocks movement, smoke blocks sight
        blk.fill(0); smk.fill(0);
        for (const u of rd.util) {
          const [t0, t1, kind, x, y, , , , r] = u; if (x == null || tt < t0 || tt > t1 || (kind !== "smoke" && kind !== "fire")) continue;
          const m = kind === "fire" ? blk : smk, rc = r / cs;
          for (let yy = Math.max(0, Math.floor(y / cs - rc)); yy <= Math.min(n - 1, Math.ceil(y / cs + rc)); yy++)
            for (let xx = Math.max(0, Math.floor(x / cs - rc)); xx <= Math.min(n - 1, Math.ceil(x / cs + rc)); xx++)
              if ((xx + .5 - x / cs) ** 2 + (yy + .5 - y / cs) ** 2 <= rc * rc) m[yy * n + xx] = 1;
        }
      }
      function bfs(d, src) {
        d.fill(-1); let h = 0, tl = 0;
        for (const c of src) if (c >= 0 && d[c] < 0) { d[c] = 0; q[tl++] = c; }
        while (h < tl) {
          const c = q[h++], cy = (c / n) | 0, cx = c - cy * n, nd = d[c] + 1;
          for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
            if (!dx && !dy) continue; const yy = cy + dy, xx = cx + dx;
            if (yy < 0 || xx < 0 || yy >= n || xx >= n) continue; const j = yy * n + xx;
            if (G[j] && !blk[j] && d[j] < 0) { d[j] = nd; q[tl++] = j; }
          }
        }
      }
      function alive(tt) {
        const a = [], b = [];
        for (let p = 0; p < NP; p++) { const sd = rd.side[p]; if (sd < 0) continue; const v = pos(p, tt); if (!v || v[3] <= 0) continue; (sd ? b : a).push(v); }
        return [a, b];
      }
      const MARGIN = 2;
      function reach(tt) {
        masks(tt); const [a, b] = alive(tt);
        bfs(dA, a.map(v => cell(v[0], v[1]))); bfs(dB, b.map(v => cell(v[0], v[1])));
        let na = 0, nb = 0, nc = 0;
        for (let i = 0; i < n * n; i++) {
          const x = dA[i], y = dB[i]; if (x < 0 && y < 0) continue;
          if (y < 0 || (x >= 0 && x + MARGIN < y)) na++; else if (x < 0 || y + MARGIN < x) nb++; else nc++;
        }
        const tot = Math.max(1, na + nb + nc); return [na / tot, nb / tot, nc / tot, a.length, b.length];
      }
      const FOV = 45 * Math.PI / 180, RANGE = Math.round(n * .45);
      function vision(tt) {
        seenA.fill(0); seenB.fill(0); const [a, b] = alive(tt);
        for (const [list, seen] of [[a, seenA], [b, seenB]]) for (const v of list) {
          const x0 = v[0] / cs, y0 = v[1] / cs, yr = v[2] * Math.PI / 180;
          for (let ang = -FOV; ang <= FOV; ang += .018) {
            const dx = Math.cos(yr + ang) * .5, dy = Math.sin(yr + ang) * .5; let x = x0, y = y0;
            for (let s = 0; s < RANGE * 2; s++) {
              x += dx; y += dy; const xx = x | 0, yy = y | 0;
              if (xx < 0 || yy < 0 || xx >= n || yy >= n) break; const j = yy * n + xx;
              if (!G[j] || smk[j]) break; seen[j] = 1;
            }
          }
        }
        let sa = 0, sb = 0, w = 0; for (let i = 0; i < n * n; i++) if (G[i]) { w++; sa += seenA[i]; sb += seenB[i]; }
        return [sa / Math.max(1, w), sb / Math.max(1, w)];
      }
      let mode = 0, lastKey = "";
      const NAMES = ["off", "reach", "vision", "both"];
      let read = null;
      function paint(tt) {
        const key = rd.n + ":" + Math.round(tt * 4) + ":" + mode;
        if (key !== lastKey) {
          lastKey = key; const px = img.data; px.fill(0);
          const r = reach(tt); let v = null;
          if (mode >= 2) v = vision(tt);
          for (let i = 0; i < n * n; i++) {
            if (!G[i]) continue; const o = i * 4;
            if (mode & 1) { const x = dA[i], y = dB[i];
              if (x >= 0 || y >= 0) {
                const own = (y < 0 || (x >= 0 && x + MARGIN < y)) ? 0 : (x < 0 || y + MARGIN < x) ? 1 : 2;
                if (own < 2) { px[o] = RGB[own][0]; px[o + 1] = RGB[own][1]; px[o + 2] = RGB[own][2]; px[o + 3] = 62; }
                else { px[o] = px[o + 1] = px[o + 2] = 240; px[o + 3] = 70; }
              } }
            if (mode >= 2 && (seenA[i] || seenB[i])) {
              const both = seenA[i] && seenB[i], c = both ? [255, 255, 255] : RGB[seenA[i] ? 0 : 1];
              px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = both ? 150 : 135;
            }
            if (smk[i] && mode >= 2) { px[o] = px[o + 1] = px[o + 2] = 210; px[o + 3] = 90; }
          }
          og.putImageData(img, 0, 0); read = [r, v];
        }
        g.save(); g.imageSmoothingEnabled = true; g.drawImage(off, 0, 0, S, S); g.restore();
        const [r, v] = read, pc = x => Math.round(100 * x) + "%";
        $("#cRead").innerHTML = `<b style="color:${SIDEHEX[0]}">Attack</b> gets to ${pc(r[0])} of the map first${v ? ` and sees ${pc(v[0])}` : ""}. <b style="color:${SIDEHEX[1]}">Defense</b> ${pc(r[1])}${v ? `, sees ${pc(v[1])}` : ""}. Contested ${pc(r[2])}.`;
      }
      function strip() {  // reach share across the whole round, one column per second
        const cvs = $("#cStrip"), sg = cvs.getContext("2d"), W = cvs.width, H = cvs.height;
        sg.fillStyle = "#07050c"; sg.fillRect(0, 0, W, H);
        const stepS = Math.max(1, Math.round(SPS)), cols = [];
        for (let tt = 0; tt < rd.nt; tt += stepS) cols.push([tt, reach(tt)]);
        const cw = W / Math.max(1, (rd.nt - 1) / stepS);
        for (const [tt, r] of cols) {
          const x = tt / (rd.nt - 1) * W; if (r[3] + r[4] === 0) continue;
          const ha = r[0] * H, hc = r[2] * H;
          sg.fillStyle = SIDEHEX[0]; sg.fillRect(x, 0, cw + .5, ha);
          sg.fillStyle = "#d9d2e6"; sg.fillRect(x, ha, cw + .5, hc);
          sg.fillStyle = SIDEHEX[1]; sg.fillRect(x, ha + hc, cw + .5, H - ha - hc);
        }
        sg.fillStyle = "#0b071299"; sg.fillRect(0, H / 2 - .5, W, 1);
        lastKey = "";
      }
      $("#bCtl").hidden = false; $("#cStrip").hidden = false;
      $("#bCtl").onclick = () => { mode = (mode + 1) % 4; $("#bCtl").textContent = "Control: " + NAMES[mode]; $("#bCtl").classList.toggle("on", mode > 0); $("#cRead").hidden = mode === 0; lastKey = ""; draw(); };
      return {paint: tt => mode && paint(tt), strip, on: () => mode > 0};
    })();

    // ---------------------------------------------------------------- 3D view
    // Players at their real heights on the radar, every bullet as a beam. Plain WebGL, no libraries.
    const G3 = (function () {
      const hasTr = D.rounds.some(r => (r.tracers && r.tracers.length) || r.z);
      if (!hasTr) return null;
      const stage = root.querySelector(".stage");
      const cvs = document.createElement("canvas"); cvs.width = cvs.height = 1024; cvs.className = "gl3d";
      const lab = document.createElement("canvas"); lab.width = lab.height = 1024; lab.className = "gl3d lab";
      stage.appendChild(cvs); stage.appendChild(lab);
      const gl = cvs.getContext("webgl", {antialias: true, premultipliedAlpha: false});
      if (!gl) { cvs.remove(); lab.remove(); return null; }
      const lg = lab.getContext("2d");
      const ppu = D.view3d?.ppu || 1, eyePx = (D.view3d?.eye || 64) * ppu;
      function sh(type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
      function prog(vs, fs) { const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); return p; }
      const PG = prog(`attribute vec3 aP;attribute vec2 aU;uniform mat4 uM;varying vec2 vU;void main(){vU=aU;gl_Position=uM*vec4(aP,1.);}`,
        `precision mediump float;varying vec2 vU;uniform sampler2D uT;uniform float uHas;void main(){vec4 c=texture2D(uT,vU);gl_FragColor=mix(vec4(.11,.08,.17,1.),vec4(c.rgb*.85,1.),uHas*c.a);}`);
      const PL = prog(`attribute vec3 aP;attribute vec3 aQ;attribute float aS;attribute vec4 aC;attribute float aW;uniform mat4 uM;uniform vec2 uR;varying vec4 vC;
        void main(){vec4 p=uM*vec4(aP,1.);vec4 q=uM*vec4(aQ,1.);vec2 a=p.xy/max(p.w,.001);vec2 b=q.xy/max(q.w,.001);vec2 d=(b-a)*uR;float l=length(d);d=l>0.?d/l:vec2(1.,0.);
        vec2 n=vec2(-d.y,d.x);p.xy+=n*aS*aW/uR*p.w;gl_Position=p;vC=aC;}`,
        `precision mediump float;varying vec4 vC;void main(){gl_FragColor=vC;}`);
      const PP = prog(`attribute vec3 aP;attribute vec4 aC;attribute float aZ;uniform mat4 uM;varying vec4 vC;void main(){gl_Position=uM*vec4(aP,1.);gl_PointSize=aZ;vC=aC;}`,
        `precision mediump float;varying vec4 vC;void main(){vec2 c=gl_PointCoord-.5;float r=length(c);if(r>.5)discard;float e=smoothstep(.5,.36,r);gl_FragColor=vec4(mix(vec3(.05),vC.rgb,step(r,.36)),vC.a*e);}`);
      const S = D.transform.size, tex = gl.createTexture(); let hasTex = 0;
      function upload(im) { gl.bindTexture(gl.TEXTURE_2D, tex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im);
        for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
        hasTex = 1; }
      const src = imgs.radar || imgs.silhouette;
      if (src) { if (src.complete && src.naturalWidth) upload(src); else src.addEventListener("load", () => { upload(src); if (on) draw(); }); }
      const ground = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, ground);
      const h = S / 2; gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-h,0,-h,0,0, h,0,-h,1,0, h,0,h,1,1, -h,0,-h,0,0, h,0,h,1,1, -h,0,h,0,1]), gl.STATIC_DRAW);
      const lineBuf = gl.createBuffer(), ptBuf = gl.createBuffer();
      // camera
      let yaw = -0.55, pitch = 0.82, dist = S * 0.95, tx = 0, tz = 0, on = false, all = false, floor = 0, Z = null;
      function persp(f, a, n, fa) { const t = 1 / Math.tan(f / 2); return [t / a,0,0,0, 0,t,0,0, 0,0,(fa + n) / (n - fa),-1, 0,0,2 * fa * n / (n - fa),0]; }
      function look(e, c) { const z = norm(sub(e, c)), x = norm(cross([0,1,0], z)), y = cross(z, x);
        return [x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0, -dot(x,e),-dot(y,e),-dot(z,e),1]; }
      const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]], dot = (a, b) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
      const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]], norm = a => { const l = Math.hypot(...a) || 1; return a.map(v => v / l); };
      function mul(a, b) { const o = new Array(16).fill(0); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) o[j*4+i] += a[k*4+i] * b[j*4+k]; return o; }
      function mvp() { const e = [tx + dist * Math.cos(pitch) * Math.sin(yaw), dist * Math.sin(pitch), tz + dist * Math.cos(pitch) * Math.cos(yaw)];
        return mul(persp(0.75, 1, 10, S * 6), look(e, [tx, 0, tz])); }
      const VX = 3;  // heights drawn 3x so floors and elevation read at map scale
      const W = (x, y, z) => [x - S / 2, (z - floor) * VX, y - S / 2];  // radar px (x right, y down) + height -> GL
      function decodeZ() { if (!rd.z) { Z = null; floor = 0; return; } const bin = atob(rd.z), u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
        Z = new Int16Array(u8.buffer); const v = []; for (let i = 0; i < Z.length; i += 7) if (Z[i] !== MISSING) v.push(Z[i] / 4); v.sort((a, b) => a - b); floor = v.length ? v[Math.floor(v.length * .05)] : 0; }
      function zAt(p, tt) { if (!Z) return floor; const i = Math.min(rd.nt - 1, Math.round(tt)); for (let d = 0; d < 6; d++) { for (const j of [i - d, i + d]) { if (j < 0 || j >= rd.nt) continue; const v = Z[j * NP + p]; if (v !== MISSING) return v / 4; } } return floor; }
      const hex = c => [parseInt(c.slice(1, 3), 16) / 255, parseInt(c.slice(3, 5), 16) / 255, parseInt(c.slice(5, 7), 16) / 255];
      const SC = SIDEHEX.map(hex);
      function seg(L, a, b, c, w) { // 6 vertices, 12 floats each: P(3) Q(3) side color(4) width
        const v = (P, Q, s) => L.push(P[0],P[1],P[2], Q[0],Q[1],Q[2], s, c[0],c[1],c[2],c[3], w);
        v(a,b,1); v(a,b,-1); v(b,a,1); v(a,b,1); v(b,a,1); v(b,a,-1); }
      function draw() {
        if (!on || !rd) return;
        if (rd !== draw._rd) { decodeZ(); draw._rd = rd; }
        gl.viewport(0, 0, cvs.width, cvs.height); gl.clearColor(.03, .02, .05, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.enable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        const M = new Float32Array(mvp());
        // ground
        gl.useProgram(PG); gl.bindBuffer(gl.ARRAY_BUFFER, ground);
        let a = gl.getAttribLocation(PG, "aP"), u = gl.getAttribLocation(PG, "aU");
        gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 3, gl.FLOAT, false, 20, 0); gl.enableVertexAttribArray(u); gl.vertexAttribPointer(u, 2, gl.FLOAT, false, 20, 12);
        gl.uniformMatrix4fv(gl.getUniformLocation(PG, "uM"), false, M); gl.uniform1f(gl.getUniformLocation(PG, "uHas"), hasTex);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex); gl.uniform1i(gl.getUniformLocation(PG, "uT"), 0);
        gl.drawArrays(gl.TRIANGLES, 0, 6); gl.disableVertexAttribArray(u);
        // lines: player stems, tracers
        const L = [], P = [], names = [];
        for (let p = 0; p < NP; p++) {
          const sd = rd.side[p]; if (sd < 0) continue; const q = pos(p, t); if (!q || q[3] <= 0) continue;
          const z = zAt(p, t), feet = W(q[0], q[1], z), head = W(q[0], q[1], z + eyePx), base = W(q[0], q[1], floor);
          seg(L, base, feet, [...SC[sd], .4], 2.5); seg(L, feet, head, [...SC[sd], 1], 9);
          const yr = q[2] * Math.PI / 180, aim = W(q[0] + Math.cos(yr) * 26, q[1] + Math.sin(yr) * 26, z + eyePx);
          seg(L, head, aim, [1, 1, 1, .75], 3);
          P.push(...head, ...SC[sd], 1, 22); names.push([head, P_name(p)]);
        }
        const win = SPS * 0.6;
        for (const r of rd.tracers || []) {
          const age = t - r[0]; if (age < 0 || (!all && age > win)) continue;
          const sd = r[1] >= 0 ? rd.side[r[1]] : 0, fade = all ? (age > win ? .35 : 1) : 1 - age / win;
          const A = W(r[2], r[3], r[4]), B = W(r[5], r[6], r[7]);
          const c = r[9] === 2 ? [1, 1, 1, .95 * fade] : r[9] === 1 ? [...SC[sd < 0 ? 0 : sd], .9 * fade] : [1, .93, .62, .55 * fade];
          seg(L, A, B, c, r[9] === 2 ? 7 : r[9] === 1 ? 4.5 : 2.6);
          if (r[9] && age <= win) P.push(...B, ...(r[9] === 2 ? [1, .35, .35] : [1, 1, 1]), fade, r[9] === 2 ? 24 : 13);
        }
        if (L.length) {
          gl.useProgram(PL); gl.bindBuffer(gl.ARRAY_BUFFER, lineBuf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(L), gl.DYNAMIC_DRAW);
          const st = 48; const at = [["aP", 3, 0], ["aQ", 3, 12], ["aS", 1, 24], ["aC", 4, 28], ["aW", 1, 44]];
          for (const [n, k, o] of at) { const l = gl.getAttribLocation(PL, n); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, k, gl.FLOAT, false, st, o); }
          gl.uniformMatrix4fv(gl.getUniformLocation(PL, "uM"), false, M); gl.uniform2f(gl.getUniformLocation(PL, "uR"), cvs.width, cvs.height);
          gl.depthMask(false); gl.drawArrays(gl.TRIANGLES, 0, L.length / 12); gl.depthMask(true);
          for (const [n] of at) gl.disableVertexAttribArray(gl.getAttribLocation(PL, n));
        }
        if (P.length) {
          gl.useProgram(PP); gl.bindBuffer(gl.ARRAY_BUFFER, ptBuf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(P), gl.DYNAMIC_DRAW);
          const at = [["aP", 3, 0], ["aC", 4, 12], ["aZ", 1, 28]];
          for (const [n, k, o] of at) { const l = gl.getAttribLocation(PP, n); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, k, gl.FLOAT, false, 32, o); }
          gl.uniformMatrix4fv(gl.getUniformLocation(PP, "uM"), false, M); gl.drawArrays(gl.POINTS, 0, P.length / 8);
          for (const [n] of at) gl.disableVertexAttribArray(gl.getAttribLocation(PP, n));
        }
        // names on a 2D overlay
        lg.clearRect(0, 0, lab.width, lab.height);
        if (names_on()) { lg.font = "600 22px 'Barlow Condensed',sans-serif"; lg.lineWidth = 4; lg.strokeStyle = "#000c"; lg.fillStyle = "#fff";
          for (const [w, n] of names) { const c = proj(M, w); if (!c) continue; lg.strokeText(n, c[0] + 10, c[1] - 10); lg.fillText(n, c[0] + 10, c[1] - 10); } }
      }
      function proj(M, w) { const x = M[0]*w[0]+M[4]*w[1]+M[8]*w[2]+M[12], y = M[1]*w[0]+M[5]*w[1]+M[9]*w[2]+M[13], ww = M[3]*w[0]+M[7]*w[1]+M[11]*w[2]+M[15];
        if (ww <= 0) return null; return [(x / ww * .5 + .5) * lab.width, (1 - (y / ww * .5 + .5)) * lab.height]; }
      // orbit controls
      let drag = null;
      lab.addEventListener("pointerdown", e => { drag = [e.clientX, e.clientY, yaw, pitch, e.shiftKey || e.button === 2]; lab.setPointerCapture(e.pointerId); });
      lab.addEventListener("pointermove", e => { if (!drag) return; const dx = e.clientX - drag[0], dy = e.clientY - drag[1];
        if (drag[4]) { const k = dist / 700; tx -= (dx * Math.cos(yaw) + dy * Math.sin(yaw)) * k; tz -= (-dx * Math.sin(yaw) + dy * Math.cos(yaw)) * k; drag[0] = e.clientX; drag[1] = e.clientY; }
        else { yaw = drag[2] - dx * .006; pitch = Math.max(.12, Math.min(1.5, drag[3] + dy * .005)); } draw(); });
      lab.addEventListener("pointerup", () => drag = null);
      lab.addEventListener("contextmenu", e => e.preventDefault());
      lab.addEventListener("wheel", e => { e.preventDefault(); dist = Math.max(80, Math.min(S * 3, dist * (e.deltaY > 0 ? 1.12 : 1 / 1.12))); draw(); }, {passive: false});
      lab.addEventListener("dblclick", () => { yaw = -0.55; pitch = 0.82; dist = S * 0.95; tx = tz = 0; draw(); });
      const P_name = p => P[p]?.player_name ?? "";
      const names_on = () => $("#bNames").classList.contains("on");
      function set(v) { on = v; stage.classList.toggle("is3d", on); $("#b3d").classList.toggle("on", on); $("#bAll").hidden = !on; if (on) draw(); }
      $("#b3d").hidden = false;
      $("#b3d").onclick = () => set(!on);
      $("#bAll").onclick = () => { all = !all; $("#bAll").classList.toggle("on", all); draw(); };
      return {draw, on: () => on, toggle: () => set(!on)};
    })();

    function draw() {
      if (!rd) return;
      const W = cv.width; g.setTransform(1, 0, 0, 1, 0, 0); g.fillStyle = "#09060f"; g.fillRect(0, 0, W, W);
      const sc = W / S * zoom; g.setTransform(sc, 0, 0, sc, ox, oy);
      const k = 1 / zoom; // keep marks readable when zoomed
      if (imgs.radar?.complete && imgs.radar.naturalWidth) { g.globalAlpha = .92; g.drawImage(imgs.radar, 0, 0, S, S); g.globalAlpha = 1; }
      else if (imgs.silhouette?.complete) g.drawImage(imgs.silhouette, 0, 0, S, S);
      if (C) C.paint(t);
      const secs = x => x / SPS;
      // utility
      if (util) for (const u of rd.util) {
        const [t0, t1, kind, x, y, ox_, oy_, who, r] = u; if (x == null) continue;
        if (kind === "smoke" || kind === "fire") {
          if (t < t0 || t > t1) continue;
          const life = Math.min(1, (t - t0) / (SPS * .6), (t1 - t) / (SPS * .8));
          g.globalAlpha = .55 * life; g.fillStyle = kind === "smoke" ? "#c9d1db" : "#ff6a00";
          g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
          g.globalAlpha = .9 * life; g.strokeStyle = kind === "smoke" ? "#eef2f6" : "#ffae66"; g.lineWidth = 1.5 * k; g.stroke(); g.globalAlpha = 1;
        } else if (t >= t0 && t - t0 < SPS * .6) {
          const a = 1 - (t - t0) / (SPS * .6);
          g.globalAlpha = a; g.fillStyle = kind === "flash" ? "#ffffff" : kind === "he" ? "#ff3b3b" : "#999";
          g.beginPath(); g.arc(x, y, (kind === "flash" ? 14 : 18) * (1.4 - a * .4), 0, 7); g.fill(); g.globalAlpha = 1;
        }
        if (ox_ != null && t >= t0 - SPS * 1.2 && t < t0) { // throw arc in flight
          const f = 1 - (t0 - t) / (SPS * 1.2); g.strokeStyle = "#ffffff55"; g.setLineDash([4 * k, 4 * k]); g.lineWidth = 1 * k;
          g.beginPath(); g.moveTo(ox_, oy_); g.lineTo(ox_ + (x - ox_) * f, oy_ + (y - oy_) * f); g.stroke(); g.setLineDash([]);
        }
      }
      // bomb
      for (const o of rd.obj) if (o[1] === "plant" && t >= o[0] && o[2] != null) {
        const pulse = .6 + .4 * Math.sin(t / SPS * 6);
        g.save(); g.translate(o[2], o[3]); g.rotate(Math.PI / 4); g.fillStyle = `rgba(255,59,59,${pulse})`; g.fillRect(-6 * k, -6 * k, 12 * k, 12 * k); g.restore();
      }
      // trails
      if (trails) for (let p = 0; p < NP; p++) {
        if (rd.side[p] < 0) continue; const n = Math.round(SPS * 3);
        g.strokeStyle = SIDEHEX[rd.side[p]]; g.lineWidth = 2 * k;
        let prev = null;
        for (let i = Math.max(0, Math.floor(t) - n); i <= Math.floor(t); i++) {
          const a = at(i, p); if (!a || a[3] <= 0) { prev = null; continue; }
          if (prev) { g.globalAlpha = .45 * (1 - (Math.floor(t) - i) / n); g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(a[0], a[1]); g.stroke(); }
          prev = a;
        }
      }
      g.globalAlpha = 1;
      // shot tracers
      if (rd.tracers && rd.tracers.length) for (const r of rd.tracers) {
        const age = t - r[0]; if (age < 0 || age > SPS * .35) continue;
        const f = 1 - age / (SPS * .35), sd = r[1] >= 0 ? rd.side[r[1]] : 0;
        g.strokeStyle = r[9] === 2 ? "#ffffff" : r[9] === 1 ? SIDEHEX[sd < 0 ? 0 : sd] : "#fff3b0";
        g.globalAlpha = (r[9] ? .95 : .55) * f; g.lineWidth = (r[9] === 2 ? 2.6 : r[9] === 1 ? 1.8 : 1.1) * k;
        g.beginPath(); g.moveTo(r[2], r[3]); g.lineTo(r[5], r[6]); g.stroke(); g.globalAlpha = 1;
      }
      else for (const s of rd.shots) {
        if (s[1] < 0 || t < s[0] || t - s[0] > SPS * .12) continue;
        const q = pos(s[1], t); if (!q || q[3] <= 0) continue;
        const yr = q[2] * Math.PI / 180; g.strokeStyle = "#fff3b0"; g.lineWidth = 1.4 * k; g.globalAlpha = .9;
        g.beginPath(); g.moveTo(q[0], q[1]); g.lineTo(q[0] + Math.cos(yr) * 70 * k, q[1] + Math.sin(yr) * 70 * k); g.stroke(); g.globalAlpha = 1;
      }
      // kill lines
      for (const kk of rd.kills) {
        const dt = t - kk[0]; if (dt < 0 || dt > SPS * 1.2 || kk[3] == null || kk[5] == null) continue;
        g.globalAlpha = 1 - dt / (SPS * 1.2); g.strokeStyle = kk[1] >= 0 ? SIDEHEX[rd.side[kk[1]]] : "#fff"; g.lineWidth = 2 * k;
        g.beginPath(); g.moveTo(kk[3], kk[4]); g.lineTo(kk[5], kk[6]); g.stroke(); g.globalAlpha = 1;
      }
      // players
      for (let p = 0; p < NP; p++) {
        const sd = rd.side[p]; if (sd < 0) continue;
        let q = pos(p, t); const ro = document.getElementById("ro" + p);
        if (!q || q[3] <= 0) {
          const la = lastAlive(p, t); if (ro) ro.classList.add("dead");
          if (la) { g.strokeStyle = SIDEHEX[sd]; g.globalAlpha = .55; g.lineWidth = 2.2 * k; const s = 5 * k;
            g.beginPath(); g.moveTo(la[0] - s, la[1] - s); g.lineTo(la[0] + s, la[1] + s); g.moveTo(la[0] - s, la[1] + s); g.lineTo(la[0] + s, la[1] - s); g.stroke(); g.globalAlpha = 1; }
          continue;
        }
        if (ro) { ro.classList.remove("dead"); ro.querySelector("i").style.width = Math.max(0, Math.min(100, q[3])) + "%"; }
        const yr = q[2] * Math.PI / 180, r = 7.5 * k;
        g.fillStyle = SIDEHEX[sd] + "33"; g.beginPath(); g.moveTo(q[0], q[1]); g.arc(q[0], q[1], 34 * k, yr - .45, yr + .45); g.closePath(); g.fill();
        g.fillStyle = SIDEHEX[sd]; g.strokeStyle = "#07050c"; g.lineWidth = 2 * k;
        g.beginPath(); g.arc(q[0], q[1], r, 0, 7); g.fill(); g.stroke();
        g.strokeStyle = "#fff"; g.lineWidth = 2 * k; g.beginPath(); g.arc(q[0], q[1], r + 2.5 * k, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * q[3] / 100); g.stroke();
        if (names) { g.font = `600 ${13 * k}px 'Barlow Condensed',sans-serif`; g.fillStyle = "#fff"; g.strokeStyle = "#000c"; g.lineWidth = 3 * k;
          g.strokeText(P[p].player_name, q[0] + 11 * k, q[1] - 9 * k); g.fillText(P[p].player_name, q[0] + 11 * k, q[1] - 9 * k); }
      }
      // HUD
      if (G3 && G3.on()) G3.draw();
      $("#hudR").textContent = `Round ${rd.n}${rd.site ? ", " + rd.site + " site" : ""}`;
      $("#hudT").textContent = clock();
      const pc = 100 * t / (rd.nt - 1); $("#tlf").style.width = pc + "%"; $("#tlh").style.left = pc + "%";
      const feed = rd.kills.filter(kk => kk[0] <= t && t - kk[0] < SPS * 6).slice(-5);
      $("#feed").innerHTML = feed.map(kk => `<div style="--c:${kk[1] >= 0 ? SIDEHEX[rd.side[kk[1]]] : "#ccc"}"><span style="color:${kk[1] >= 0 ? SIDEHEX[rd.side[kk[1]]] : "#ccc"}">${esc(P[kk[1]]?.player_name ?? "world")}</span>
        <span style="color:var(--dim)">${esc(gun(kk[7]))}${kk[8] ? " (head)" : ""}</span>
        <span style="color:${kk[2] >= 0 ? SIDEHEX[rd.side[kk[2]]] : "#ccc"}">${esc(P[kk[2]]?.player_name ?? "?")}</span></div>`).join("");
    }

    function frame(ts) {
      if (!playing) return;
      const dt = last ? (ts - last) / 1000 : 0; last = ts;
      t += dt * SPS * speed;
      if (t >= rd.nt - 1) { t = rd.nt - 1; setPlay(false); }
      draw(); raf(frame);
    }
    function setPlay(v) { playing = v; last = 0; $("#bPlay").textContent = v ? "❚❚ Pause" : "▶ Play"; if (v) raf(frame); }
    const step = d => { const i = D.rounds.findIndex(r => r.n === rd.n) + d; if (i >= 0 && i < D.rounds.length) load(D.rounds[i].n); };
    $("#bPlay").onclick = () => {
      if (V && V.drives()) { V.toggle(); return; }
      if (t >= rd.nt - 1) t = 0; setPlay(!playing); };
    $("#bPrev").onclick = () => step(-1); $("#bNext").onclick = () => step(1);
    root.querySelectorAll(".ctrl [data-s]").forEach(b => b.onclick = () => { speed = +b.dataset.s; root.querySelectorAll(".ctrl [data-s]").forEach(x => x.classList.toggle("on", x === b)); });
    const tog = (id, f) => $(id).onclick = e => { e.currentTarget.classList.toggle("on", f()); draw(); };
    tog("#bTrail", () => trails = !trails); tog("#bNames", () => names = !names); tog("#bUtil", () => util = !util);
    on(window, "keydown", e => {
      if (!$("#replay").classList.contains("on") || e.target.tagName === "SELECT") return;
      if (e.code === "Space") { e.preventDefault(); $("#bPlay").click(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); t = Math.min(rd.nt - 1, t + SPS * 5); draw(); userSeek(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); t = Math.max(0, t - SPS * 5); draw(); userSeek(); }
      else if (e.key === "]") step(1); else if (e.key === "[") step(-1);
      else if (e.key === "t") $("#bTrail").click(); else if (e.key === "n") $("#bNames").click();
      else if (e.key === "c" && C) $("#bCtl").click();
      else if (e.key === "v" && G3) G3.toggle();
    });
    // zoom & pan (in canvas pixel space)
    const toCanvas = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
    cv.addEventListener("wheel", e => { e.preventDefault(); const [mx, my] = toCanvas(e), f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const nz = Math.max(1, Math.min(8, zoom * f)); const kf = nz / zoom; ox = mx - (mx - ox) * kf; oy = my - (my - oy) * kf; zoom = nz; if (zoom === 1) ox = oy = 0; draw(); }, {passive: false});
    cv.addEventListener("pointerdown", e => { const [sx, sy] = toCanvas(e), [x0, y0] = [ox, oy];
      const mv = ev => { const [x, y] = toCanvas(ev); ox = x0 + x - sx; oy = y0 + y - sy; draw(); };
      window.addEventListener("pointermove", mv); window.addEventListener("pointerup", () => window.removeEventListener("pointermove", mv), {once: true}); });
    cv.addEventListener("dblclick", () => { zoom = 1; ox = oy = 0; draw(); });
    if (D.rounds.length) load(D.rounds[0].n);
    else $("#replay").innerHTML = `<div class="empty">Replay data not included.</div>`;
    function seekTick(n, tick, from) {
      load(n); t = Math.max(0, Math.min(rd.nt - 1, (tick - rd.t0) / STEP)); draw(); userSeek();
      const b = $("#bBackD"); b.classList.toggle("show", !!from);
      if (from) b.onclick = () => { b.classList.remove("show"); root.querySelector('#tabs button[data-t="' + from + '"]').click(); };
    }
    return {load, draw, setPos, seekTick, pos: () => [rd, t], STEP, TR, SPS};
  })();

  // ---------------------------------------------------------------- video sync
  V = (function () {
    const vids = D.videos || [];
    if (!vids.length || !D.rounds.length) return null;
    $("#replayGrid").classList.add("withvideo");
    const TR = R.TR, STEP = R.STEP, key = "unitick.sync." + D.match_key;
    let saved = {}; try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch (e) {}
    vids.forEach((v, i) => {
      const s = saved[i] || {};
      v._anchors = Object.assign(Object.fromEntries((v.anchors || []).map(a => [a[0], a[1]])), s.anchors || {});
      v._offset = s.offset ?? v.offset ?? 0;
    });
    const persist = () => { try { localStorage.setItem(key, JSON.stringify(Object.fromEntries(vids.map((v, i) => [i, {anchors: v._anchors, offset: v._offset}])))); } catch (e) {} };
    const byN = Object.fromEntries(D.rounds.map((r, i) => [r.n, i]));
    const tickOf = (rd, t) => rd.t0 + t * STEP;
    let cur = 0, P = null, locked = true, estimated = false, broken = false;

    // game position -> video seconds (null if unknown / not covered)
    function toVideo(v, rd, t) {
      const tick = tickOf(rd, t); estimated = false;
      if (v.kind === "youtube") {
        if (v._anchors[rd.n] != null) return v._anchors[rd.n] + (tick - rd.live_tick) / TR;
        const i = byN[rd.n]; let best = null;
        for (let d = 1; d < D.rounds.length && best == null; d++) for (const j of [i - d, i + d]) {
          const r = D.rounds[j]; if (r && v._anchors[r.n] != null) { best = v._anchors[r.n] + (tick - r.live_tick) / TR; break; } }
        estimated = true; return best;
      }
      const base = (D.wall.end && v.wall_start != null && D.wall.end_tick != null)
        ? D.wall.end - (D.wall.end_tick - tick) / TR - v.wall_start
        : (tick - D.rounds[0].t0) / TR;
      return base + v._offset;
    }
    // video seconds -> [round, t] (the round whose mapped span contains it)
    function fromVideo(v, vs) {
      let bestR = null, bestD = 1e9;
      for (const rd of D.rounds) {
        const a = toVideo(v, rd, 0), b = toVideo(v, rd, rd.nt - 1);
        if (a == null) continue;
        if (vs >= a && vs <= b) return [rd.n, (vs - a) * TR / STEP];
        const d = Math.min(Math.abs(vs - a), Math.abs(vs - b)); if (d < bestD) { bestD = d; bestR = [rd.n, vs < a ? 0 : rd.nt - 1]; }
      }
      return bestR;
    }
    // pick the clip that covers a moment (many 30 s clips -> one each)
    function covering(rd, t) {
      const v0 = vids[cur], s0 = toVideo(v0, rd, t);
      if (v0.kind !== "file" || s0 == null || !v0.duration || (s0 >= 0 && s0 <= v0.duration)) return cur;
      const i = vids.findIndex(v => { const s = toVideo(v, rd, t); return v.kind === "file" && s != null && s >= 0 && s <= (v.duration || 0); });
      return i >= 0 ? i : cur;
    }

    // ---- players (YouTube iframe API or <video>)
    const box = $("#vbox");
    // Many VODs (ESL etc.) embed on http://localhost but refuse http://127.0.0.1 - same machine, different hostname.
    const ipHost = /^(?:\d{1,3}\.){3}\d{1,3}$/.test(location.hostname) || location.hostname === "[::1]";
    const localHostUrl = () => `http://localhost${location.port ? ":" + location.port : ""}${location.pathname}${location.search}${location.hash}`;
    function msg(html) { box.innerHTML = `<div class="vmsg"><p>${html}</p></div>`; }
    function ytHref(v, rd) {
      const s0 = rd ? toVideo(v, rd, rd.live) : null;
      return `https://www.youtube.com/watch?v=${v.id}` + (s0 != null ? `&t=${Math.max(0, Math.floor(s0))}s` : "");
    }
    function ytFail(v, i, detail) {
      if (cur !== i) return;
      broken = true; P = null;
      if (locked) { locked = false; $("#vLock").classList.remove("on"); }
      const [rd] = R.pos(), href = ytHref(v, rd);
      if (ipHost) {
        msg(`YouTube will not embed this broadcast on <code>${esc(location.hostname)}</code>.<br>`
          + `<a href="${localHostUrl()}">Open this page as localhost</a> (same app), or `
          + `<a href="${href}" target="_blank" rel="noopener">watch on YouTube</a>.<br>`
          + `Map <b>Play</b> still works - Lock is off while the embed is unavailable.`);
      } else {
        msg(`This broadcast cannot play inside unitick${detail ? ` (${esc(detail)})` : ""}.<br>`
          + `<a href="${href}" target="_blank" rel="noopener">Watch it on YouTube</a>`
          + (rd && toVideo(v, rd, rd.live) != null ? ` (opens near round ${rd.n})` : "") + `.<br>`
          + `Map <b>Play</b> still works - Lock is off while the embed is unavailable.`);
      }
      status();
    }
    function mount(i) {
      cur = i; P = null; broken = false; const v = vids[i]; $("#vSel").value = i;
      $("#vOpen").href = v.kind === "youtube" ? `https://www.youtube.com/watch?v=${v.id}` : (location.protocol === "file:" ? v.file_url : "/_media/" + v.media);
      if (v.kind === "youtube") {
        if (location.protocol === "file:") {
          msg(`YouTube does not play inside a report opened as a file.<br>Run <code>unitick serve</code> (START_HERE option 6) to watch it here in sync,<br>or press <b>Open video</b> to watch it on YouTube.`); status(); return; }
        // Prefer fixing the host over showing a dead player: ESL and similar VODs reject IP origins.
        if (ipHost) { ytFail(v, i, "open via localhost"); return; }
        box.innerHTML = `<div id="ytp"></div>`;
        const make = () => {
          const yp = new YT.Player("ytp", {videoId: v.id, playerVars: {rel: 0, playsinline: 1, origin: location.origin},
            events: {
              onReady: () => {
                if (cur !== i) return;
                let data = {};
                try { data = yp.getVideoData() || {}; } catch (e) {}
                if (data.isPlayable === false || data.errorCode) {
                  ytFail(v, i, data.errorCode === "auth" ? "embedding blocked by YouTube" : (data.errorCode || "not playable"));
                  return;
                }
                P = {time: () => yp.getCurrentTime(), seek: s => yp.seekTo(Math.max(0, s), true), play: () => yp.playVideo(), pause: () => yp.pauseVideo(), playing: () => yp.getPlayerState() === 1};
                follow(...R.pos());
              },
              onError: e => {
                const codes = {2: "invalid video id", 5: "player error", 100: "video not found", 101: "embedding disabled", 150: "embedding disabled", 153: "missing referrer"};
                ytFail(v, i, codes[e.data] || ("error " + e.data));
              },
            }});
        };
        if (window.YT && YT.Player) make(); else { window.onYouTubeIframeAPIReady = make; if (!document.getElementById("ytapi")) { const sc = el("script", {id: "ytapi", src: "https://www.youtube.com/iframe_api"}); document.head.appendChild(sc); } }
        // Hosts that block third-party players (sandboxed pages, strict CSP): fall back to a link.
        setTimeout(() => { if (!P && !broken && cur === i && vids[i].kind === "youtube") ytFail(v, i, "embed did not load"); }, 5000);
      } else {
        const src = location.protocol === "file:" ? v.file_url : "/_media/" + v.media;
        box.innerHTML = `<video id="vel" controls playsinline preload="metadata" src="${esc(src)}"></video>`;
        const ve = $("#vel");
        ve.onerror = () => { broken = true; P = null; if (locked) { locked = false; $("#vLock").classList.remove("on"); }
          msg(`Could not load <b>${esc(v.title)}</b>.<br>Browsers play .mp4 / .webm; if this is .mkv, remux it to .mp4 (OBS: File → Remux Recordings).`); };
        P = {time: () => ve.currentTime, seek: s => { ve.currentTime = Math.max(0, s); }, play: () => ve.play(), pause: () => ve.pause(), playing: () => !ve.paused && !ve.ended};
        ve.onloadedmetadata = () => { if (!v.duration) v.duration = ve.duration; follow(...R.pos()); };
      }
      status();
    }
    vids.forEach((v, i) => $("#vSel").appendChild(el("option", {value: i}, esc(v.title || (v.kind === "youtube" ? "broadcast" : "recording " + (i + 1))))));
    $("#vSel").onchange = e => mount(+e.target.value);

    function follow(rd, t) {
      if (!P || !rd) return;
      const i = covering(rd, t); if (i !== cur) { mount(i); return; }
      const s = toVideo(vids[cur], rd, t);
      if (s != null && Math.abs(P.time() - s) > 0.4) P.seek(s);
      status();
    }
    function status() {
      const v = vids[cur], [rd, t] = R.pos(); if (!rd) return;
      let txt;
      if (v.kind === "youtube") {
        const n = Object.keys(v._anchors).length;
        txt = n ? `<b>${n}/${D.rounds.length}</b> rounds synced` + (v._anchors[rd.n] == null ? `. Round ${rd.n} <b>estimated</b> from a nearby round (broadcast pauses cause drift): press Sync at this round's start` : `. Round ${rd.n} synced`)
                : `<b>not synced yet</b>: play the video to the moment round ${rd.n} goes live, set the map to the same moment, press <b>Sync here</b>. One sync per round; Export sync to share it.`;
      } else {
        const auto = D.wall.end && v.wall_start != null;
        const s = toVideo(v, rd, t), out = v.duration && s != null && (s < 0 || s > v.duration);
        txt = (auto ? "auto-synced by file time" : "synced from round 1") + `, offset <b>${v._offset >= 0 ? "+" : ""}${v._offset.toFixed(1)}s</b>` + (out ? ". <b>No recording covers this moment.</b>" : "");
      }
      $("#vStat").innerHTML = txt;
    }
    function sync() {
      if (!P) return; const v = vids[cur], [rd, t] = R.pos(); const now = P.time();
      if (v.kind === "youtube") v._anchors[rd.n] = now - (tickOf(rd, t) - rd.live_tick) / TR;
      else v._offset += now - toVideo(v, rd, t);
      persist(); status();
    }
    function nudge(d) {
      const v = vids[cur], [rd] = R.pos();
      if (v.kind === "youtube") { if (v._anchors[rd.n] == null) return; v._anchors[rd.n] += d; } else v._offset += d;
      persist(); follow(...R.pos());
    }
    $("#vSync").onclick = sync; $("#vMinus").onclick = () => nudge(-0.5); $("#vPlus").onclick = () => nudge(0.5);
    $("#vLock").onclick = e => { locked = !locked; e.currentTarget.classList.toggle("on", locked); };
    $("#vExport").onclick = () => {
      const out = {videos: vids.map(v => { const o = {kind: v.kind, title: v.title}; if (v.id) o.id = v.id;
        if (v.kind === "youtube") o.anchors = Object.entries(v._anchors).map(([n, s]) => [+n, Math.round(s * 10) / 10]).sort((a, b) => a[0] - b[0]);
        else o.offset = Math.round(v._offset * 10) / 10; return o; })};
      const text = JSON.stringify(out, null, 1);
      try { navigator.clipboard.writeText(text).then(() => { $("#vStat").innerHTML += ". <b>Copied video.json to the clipboard.</b>"; }, () => {}); } catch (e) {}
      try { const a = el("a", {href: URL.createObjectURL(new Blob([text], {type: "application/json"})), download: "video.json"}); a.click(); } catch (e) {}
    };
    // map follows the video while it plays
    (function loop() {
      if (P && locked && P.playing()) { const pos = fromVideo(vids[cur], P.time()); if (pos) R.setPos(pos[0], pos[1]); if (Math.random() < 0.05) status(); }
      raf(loop);
    })();
    mount(0);
    // When Lock is on, Play drives the video (map follows). If the embed is broken, drives() is false so Play animates the map.
    return {follow, drives: () => !!P && locked && !broken, toggle: () => { if (!P || broken) return; P.playing() ? P.pause() : P.play(); }};
  })();
  return () => {
    alive = false;
    ac.abort();
    root.querySelectorAll("video").forEach(v => { try { v.pause(); } catch (e) { /* already gone */ } });
    root.innerHTML = "";
  };
}
