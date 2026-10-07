"use strict";
/* Pulp Magazine Explorer: static, no build step. Data comes from data/*.json (pipeline/export.py). */

const main = document.getElementById("main");
const input = document.getElementById("q");
const list = document.getElementById("suggestions");
const statusEl = document.getElementById("search-status");
const tooltip = document.getElementById("tooltip");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KIND = { a: "Author", w: "Story", m: "Magazine" };
const NONFICTION = new Set(["essay", "review", "editor", "interview"]);

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = s => String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const fmtDate = d => {
  if (!d) return "";
  const [y, m] = d.split("-");
  if (!y || y === "0000") return "";
  return +m ? `${MONTHS[+m - 1]} ${y}` : y;
};
const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
const bookreader = (ia, n) => `https://archive.org/details/${encodeURIComponent(ia)}` + (n == null ? "" : `/page/n${n}`);

const cache = new Map();
function load(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(`data/${path}`).then(r => {
      if (!r.ok) throw new Error(`${path}: ${r.status}`);
      return r.json();
    }).catch(e => { cache.delete(path); throw e; }));
  }
  return cache.get(path);
}

/* ---------- Routing ---------- */

const routes = [
  [/^#\/?$/, home],
  [/^#\/author\/(\d+)(?:\/w(\d+))?$/, author],
  [/^#\/issue\/(\d+)$/, issue],
  [/^#\/magazines$/, magazines],
  [/^#\/magazine\/(.+)$/, magazine],
  [/^#\/about$/, about],
];

async function route() {
  const hash = location.hash || "#/";
  closeSuggestions();
  for (const a of document.querySelectorAll(".masthead nav a")) {
    a.toggleAttribute("aria-current", hash.startsWith(a.getAttribute("href")));
    if (a.hasAttribute("aria-current")) a.setAttribute("aria-current", "page");
  }
  for (const [re, view] of routes) {
    const m = hash.match(re);
    if (m) {
      main.innerHTML = `<p class="loading">Loading…</p>`;
      try {
        await view(...m.slice(1).map(x => x && decodeURIComponent(x)));
      } catch (e) {
        console.error(e);
        main.innerHTML = `<h1>Not found</h1><p class="empty">That page doesn't exist or couldn't be loaded. <a href="#/">Back to the start</a>.</p>`;
      }
      if (!m[2]) { window.scrollTo(0, 0); main.focus({ preventScroll: true }); }
      return;
    }
  }
  main.innerHTML = `<h1>Not found</h1><p><a href="#/">Back to the start</a>.</p>`;
}
window.addEventListener("hashchange", route);

/* ---------- Views ---------- */

async function home() {
  const s = await load("stats.json");
  document.title = "Pulp Magazine Explorer";
  main.innerHTML = `
    <h1>Find a story in the pulps</h1>
    <p class="lede">Search an author, a pen name or a story title and jump straight to the page
      of the scanned magazine where it begins, across ${s.issues.toLocaleString()} issues
      of science fiction, fantasy and weird-fiction magazines from ${s.first_year} to ${s.last_year}.</p>
    <ul class="stats">
      <li><b>${s.issues.toLocaleString()}</b><span>scanned issues</span></li>
      <li><b>${s.works.toLocaleString()}</b><span>stories, articles and features</span></li>
      <li><b>${s.authors.toLocaleString()}</b><span>authors</span></li>
      <li><b>${s.magazines.toLocaleString()}</b><span>magazines</span></li>
    </ul>
    <h2>Try</h2>
    <ul class="try">
      ${["H. P. Lovecraft", "Robert E. Howard", "Isaac Asimov", "C. L. Moore", "Lewis Padgett", "Weird Tales", "Philip K. Dick", "Leigh Brackett"]
        .map(t => `<li><button type="button" class="chip" data-try="${esc(t)}">${esc(t)}</button></li>`).join("")}
    </ul>
    <h2>Browse</h2>
    <p><a href="#/magazines">All ${s.magazines} magazines</a>, by issue.</p>`;
  for (const b of main.querySelectorAll("[data-try]")) {
    b.addEventListener("click", () => { input.value = b.dataset.try; input.focus(); suggest(); });
  }
}

async function author(id, workId) {
  const a = await load(`authors/${id}.json`);
  document.title = `${a.name} · Pulp Magazine Explorer`;
  const life = [a.birth, a.death].some(Boolean) ? ` (${a.birth || "?"}–${a.death || ""})` : "";
  const apps = a.works.flatMap(w => w.apps);
  const fiction = a.works.filter(w => w.fiction);
  const other = a.works.filter(w => !w.fiction);
  const extras = [];
  if (a.legal_name && norm(a.legal_name) !== norm(a.name)) extras.push(`Legal name: ${esc(a.legal_name.replace(/^(.*), (.*)$/, "$2 $1"))}`);
  if (a.pen_names.length) extras.push(`Also wrote as ${a.pen_names.map(esc).join(", ")}`);
  if (a.pen_name_of.length) extras.push(`A pen name of ${a.pen_name_of.map(([pid, n]) => `<a href="#/author/${pid}">${esc(n)}</a>`).join(" and ")}`);

  main.innerHTML = `
    <h1>${esc(a.name)}<span class="meta">${esc(life)}</span></h1>
    ${extras.map(e => `<p class="sub">${e}</p>`).join("")}
    <p class="meta">${plural(fiction.length, "work")} of fiction${other.length ? ` and ${plural(other.length, "article")}, review${other.length === 1 ? "" : "s"} or feature${other.length === 1 ? "" : "s"}` : ""}
      in ${plural(new Set(apps.map(x => x.issue)).size, "scanned issue")} ·
      <a href="https://www.isfdb.org/cgi-bin/ea.cgi?${a.id}">Full bibliography on ISFDB</a></p>
    ${timeline(apps)}
    <div class="filters" role="group" aria-label="Show">
      <button type="button" data-f="fiction" aria-pressed="${fiction.length ? "true" : "false"}">Fiction (${fiction.length})</button>
      <button type="button" data-f="other" aria-pressed="${fiction.length ? "false" : "true"}">Articles &amp; features (${other.length})</button>
      <button type="button" data-f="all" aria-pressed="false">All (${a.works.length})</button>
    </div>
    <ul class="works" id="works"></ul>`;
  const show = f => {
    const ws = f === "all" ? a.works : f === "fiction" ? fiction : other;
    document.getElementById("works").innerHTML = ws.map(workCard).join("") || `<li class="empty">Nothing here.</li>`;
    for (const b of main.querySelectorAll("[data-f]")) b.setAttribute("aria-pressed", String(b.dataset.f === f));
  };
  for (const b of main.querySelectorAll("[data-f]")) b.addEventListener("click", () => show(b.dataset.f));
  const target = workId && a.works.find(w => String(w.id) === workId);
  show(target ? (target.fiction ? "fiction" : "other") : fiction.length ? "fiction" : "other");
  wireChart();
  if (target) {
    const el = document.getElementById(`w${workId}`);
    if (el) requestAnimationFrame(() => { el.scrollIntoView({ block: "start", behavior: "instant" }); el.focus({ preventScroll: true }); });
  }
}

function workCard(w) {
  const reprints = w.apps.length > 1 ? `<span class="tag">${w.apps.length} appearances</span>` : "";
  return `<li class="work" id="w${w.id}" tabindex="-1">
    <div class="work-head"><h3>${esc(w.title)}</h3><span class="tag">${esc(w.type)}</span>${reprints}</div>
    <ul class="apps">${w.apps.map(x => appearance(x, w.title)).join("")}</ul>
  </li>`;
}

function appearance(x, workTitle) {
  const notes = [];
  if (norm(x.printed_title) !== norm(workTitle)) notes.push(`as “${esc(x.printed_title)}”`);
  if (x.as.length) notes.push(`by ${x.as.map(esc).join(" & ")}`);
  return `<li>
    <span class="when">${esc(fmtDate(x.date) || "undated")}</span>
    <a href="#/issue/${x.issue}">${esc(x.issue_title)}</a>
    ${notes.length ? `<span class="note">${notes.join(", ")}</span>` : ""}
    ${readLinks(x.scans, x.page)}
  </li>`;
}

function readLinks(scans, page, stampAuto = true) {
  return scans.map((s, i) => {
    const which = scans.length > 1 ? ` (scan ${i + 1})` : "";
    const auto = s.auto && stampAuto ? `<span class="tag auto" title="Matched automatically: ${esc(s.auto)}">auto-matched</span>` : "";
    return auto + (s.exact
      ? `<a class="read" href="${bookreader(s.ia, s.n)}">Read${page ? ` from p. ${esc(page)}` : ""}${which} ↗</a>`
      : `<a class="read approx" href="${bookreader(s.ia, null)}" title="We couldn't locate the exact page in this scan">Open issue${page ? `, p. ${esc(page)}` : ""}${which} ↗</a>`);
  }).join("");
}

async function issue(id) {
  const iss = await load(`issues/${id}.json`);
  document.title = `${iss.title} · Pulp Magazine Explorer`;
  const entries = iss.contents.map(c => `<li>
      <div class="line">
        ${c.by.length ? `<a class="toc-title" href="#/author/${c.by[0][0]}/w${c.work}">${esc(c.t)}</a>` : `<span class="toc-title">${esc(c.t)}</span>`}
        <span class="leader" aria-hidden="true"></span>
        <span class="pg">${c.page ? `<span class="visually-hidden">page </span>${esc(c.page)}` : ""}</span>
      </div>
      <div class="toc-by"><span class="tag">${esc(c.type)}</span>
        ${c.by.length ? `by ${c.by.map(([aid, n]) => `<a href="#/author/${aid}">${esc(n)}</a>`).join(" & ")}` : ""}
        ${readLinks(c.scans, "", false)}</div>
    </li>`).join("");
  main.innerHTML = `
    <p class="meta"><a href="#/magazine/${encodeURIComponent(iss.magazine)}">${esc(iss.magazine)}</a></p>
    <h1>${esc(iss.title)}</h1>
    ${iss.editors.length ? `<p class="sub">Edited by ${iss.editors.map(([aid, n]) => `<a href="#/author/${aid}">${esc(n)}</a>`).join(" & ")}</p>` : ""}
    <p class="sub">${iss.scans.map((ia, i) => `<a href="${bookreader(ia, null)}">Open the scan${iss.scans.length > 1 ? ` ${i + 1}` : ""} on archive.org ↗</a>`).join(" · ")}
      · <a href="https://www.isfdb.org/cgi-bin/pl.cgi?${iss.id}">ISFDB record</a></p>
    ${Object.entries(iss.auto || {}).map(([ia, ev]) => `<p class="notice">${iss.scans.length > 1 ? `Scan ${iss.scans.indexOf(ia) + 1} was` : "This scan was"}
      matched to this issue automatically (${esc(ev)}), not by an ISFDB editor.
      <a href="#/about">How matching works</a>.</p>`).join("")}
    <section class="contents-page" aria-labelledby="toc-h">
      <h2 id="toc-h">Contents</h2>
      ${entries ? `<ol class="toc-list">${entries}</ol>` : `<p class="empty">ISFDB lists no contents for this issue.</p>`}
    </section>`;
}

async function magazines() {
  const mags = await load("magazines.json");
  document.title = "Magazines · Pulp Magazine Explorer";
  main.innerHTML = `<h1>Magazines</h1>
    <p class="sub">${mags.length} magazines, by number of indexed issues. Names and groupings follow ISFDB.
      “Not indexed” counts scans we found for a magazine but couldn't tie to a specific issue.</p>
    <ul class="mag-list">${mags.map(m => `<li><a href="#/magazine/${encodeURIComponent(m.name)}">${esc(m.name)}</a>
      <span class="meta">${m.issues.length}${m.other.length ? ` + ${m.other.length} not indexed` : ""}</span></li>`).join("")}</ul>`;
}

async function magazine(name) {
  const mags = await load("magazines.json");
  const m = mags.find(x => x.name === name);
  if (!m) throw new Error("no such magazine");
  document.title = `${m.name} · Pulp Magazine Explorer`;
  const byYear = new Map();
  for (const [id, title, date] of m.issues) {
    const y = (date || "").slice(0, 4) && date.slice(0, 4) !== "0000" ? date.slice(0, 4) : "Undated";
    if (!byYear.has(y)) byYear.set(y, []);
    byYear.get(y).push([id, title, date]);
  }
  const otherByYear = new Map();
  for (const [ia, title, year, reason] of m.other) {
    const y = year ? String(year) : "Undated";
    if (!otherByYear.has(y)) otherByYear.set(y, []);
    otherByYear.get(y).push([ia, title, reason]);
  }
  main.innerHTML = `<p class="meta"><a href="#/magazines">Magazines</a></p>
    <h1>${esc(m.name)}</h1>
    <p class="sub">${plural(m.issues.length, "indexed issue")}${m.other.length ? `, plus <button type="button" class="linkish" data-jump="other">${m.other.length} more ${m.other.length === 1 ? "scan" : "scans"} not indexed</button>` : ""}</p>
    ${m.issues.length ? `<dl class="years">${[...byYear].map(([y, iss]) => `<dt>${esc(y)}</dt><dd>${iss.map(([id, title, date]) =>
      `<a class="chip" href="#/issue/${id}" aria-label="${esc(title)}">${esc(+date.slice(5, 7) ? MONTHS[+date.slice(5, 7) - 1] : title)}</a>`).join("")}</dd>`).join("")}</dl>`
      : `<p class="empty">None of this magazine's scans could be tied to a specific issue yet.</p>`}
    ${m.other.length ? `<section class="other" aria-labelledby="other">
      <h2 id="other" tabindex="-1">Also scanned, not indexed</h2>
      <div class="notice">
        <p><b>These scans are on archive.org but aren't indexed here.</b> We couldn't confirm which issue each one is, so they
          have no table of contents and no links to individual stories.</p>
        <p>They're listed under the titles their uploaders gave them. We filed them on this page because their titles name
          this magazine, and grouped them by the year in the title, so an occasional scan may be under the wrong year or
          even the wrong magazine. Opening one takes you to the scan on archive.org, where you can check it yourself.</p>
        <p>Why a scan isn't indexed: <i>issue not confirmed</i> means its text didn't clearly contain the stories of any
          one ISFDB issue (often poor OCR, or more than one issue fits); <i>no readable text</i> means archive.org has no
          OCR text for it. <a href="#/about">More about how scans are matched</a>.</p>
      </div>
      <dl class="years other-list">${[...otherByYear].map(([y, scans]) => `<dt>${esc(y)}</dt><dd><ul>${scans.map(([ia, title, reason]) =>
        `<li><a href="${bookreader(ia, null)}">${esc(title)} ↗</a> <span class="meta">${esc(reason)}</span></li>`).join("")}</ul></dd>`).join("")}</dl>
    </section>` : ""}`;
  main.querySelector("[data-jump]")?.addEventListener("click", () => {
    const h = document.getElementById("other");
    h.scrollIntoView({ block: "start" });
    h.focus({ preventScroll: true });
  });
}

async function about() {
  const s = await load("stats.json");
  document.title = "About · Pulp Magazine Explorer";
  main.innerHTML = `<div class="prose">
    <h1>About this tool</h1>
    <p>The Internet Archive holds tens of thousands of scanned pulp magazines. The
      <a href="https://www.isfdb.org/">Internet Speculative Fiction Database</a> (ISFDB), built by volunteers,
      records what's in each issue: every story, who wrote it, under which name, and on which page it starts.
      This tool joins the two, so you can go from an author or a story straight to the scanned page.</p>
    <h2>Where the data comes from</h2>
    <ul>
      <li><b>Which scan is which issue.</b> ISFDB editors have linked ${(s.scans - s.auto_scans).toLocaleString()} scans in the Archive's
        <a href="https://archive.org/details/pulpmagazinearchive">Pulp Magazine Archive</a> to ISFDB's issue records.
        Another ${s.auto_scans.toLocaleString()} scans were matched automatically, adding ${s.auto_issues.toLocaleString()} issues
        no editor had linked. A match starts from the magazine name and cover date in the scan's title, and is accepted
        only if most of that issue's distinctive story titles are actually found in the scan's text, one candidate
        clearly beats the others (such as the next month's issue, or a British reprint edition of the same month), and the
        page counts agree. Tested against the editors' own links, every one of 2,076 automatic matches agreed (in three
        cases the scan's cover showed the editors' link was the one in error), and all 50 new matches checked by eye were
        right. Automatic matches are labelled on the site.</li>
      <li><b>Contents, authors and pen names</b> come from ISFDB's public database backup of ${esc(s.isfdb_backup)},
        used under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Stories reprinted or retitled are
        grouped as one work, the way ISFDB groups them.</li>
      <li><b>The page a story starts on.</b> ISFDB gives the printed page number, but a scan counts pages from its
        cover, and unnumbered ads, plates and inserts shift the count. We read the printed page numbers from the
        scan's OCR text (or use the Archive's own page-number detection), then find the scan page that carries the
        story's printed page.</li>
    </ul>
    <h2>How accurate it is</h2>
    <p>${s.exact_links.toLocaleString()} links go to a specific page. In hand-checks of 110 random links against the page
      images, 108 opened on the first page of the right story. One miss was thrown off by a bound-in subscription insert;
      the other fell in a stretch of pages missing from the scan, and links in stretches like that now open the issue instead.
      Where the page couldn't be located (mostly unnumbered front matter, or pages whose numbers didn't survive OCR),
      the link opens the issue instead and says so.</p>
    <p class="notice">Page links come from imperfect OCR. If a link lands on the wrong page, the story is almost always
      a page or two away in the same scan.</p>
    <h2>What's not here (yet)</h2>
    <ul>
      <li>Scans the matcher couldn't confirm (poor OCR, missing dates, or more than one plausible issue) aren't indexed:
        no story is ever linked to a scan unless its issue was confirmed. ${s.other_scans.toLocaleString()} of them are
        still listed, under “Also scanned, not indexed” on their magazine's page, by the uploader's own title, so you
        can find and check them yourself. They're filed by the magazine and year in that title, which is usually but not
        always right.</li>
      <li>Westerns, detective pulps, dime novels and other magazines ISFDB doesn't index.</li>
    </ul>
    <h2>Credits</h2>
    <p>Bibliographic data © ISFDB
      contributors, CC BY 4.0. Scans courtesy of the Internet Archive and the people who uploaded them.
      Type: Anton (Vernon Adams) and Alfa Slab One (JM Solé), SIL Open Font License.</p>
  </div>`;
}

/* ---------- Timeline chart (single series: appearances per year) ---------- */

function timeline(apps) {
  const counts = new Map();
  for (const x of apps) if (x.year) counts.set(x.year, (counts.get(x.year) || 0) + 1);
  if (!counts.size) return "";
  const years = [...counts.keys()];
  let y0 = Math.min(...years), y1 = Math.max(...years);
  if (y1 - y0 < 9) { const pad = Math.ceil((9 - (y1 - y0)) / 2); y0 -= pad; y1 += pad; }
  const max = Math.max(...counts.values());
  const W = Math.max(300, Math.min(900, main.clientWidth - 34)), H = 150, L = 28, B = 22, T = 8;
  const slot = (W - L) / (y1 - y0 + 1);
  const bw = Math.max(2, Math.min(24, slot - 2));
  const r = Math.min(4, bw / 2);
  const ys = v => T + (H - T - B) * (1 - v / max);
  const ticks = max <= 1 ? [max] : [Math.ceil(max / 2), max];
  let svg = `<g class="axis" aria-hidden="true">`;
  for (const t of ticks) svg += `<line class="grid" x1="${L}" x2="${W}" y1="${ys(t)}" y2="${ys(t)}"/><text x="${L - 6}" y="${ys(t) + 4}" text-anchor="end">${t}</text>`;
  svg += `<line class="grid" x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}"/>`;
  const perLabel = W / 50;  // room for about one year label per 50px
  const step = [5, 10, 20, 25, 50].find(st => (y1 - y0) / st <= perLabel) || 50;
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
    svg += `<text x="${L + (y - y0 + .5) * slot}" y="${H - 5}" text-anchor="middle">${y}</text>`;
  }
  svg += `</g>`;
  for (const [y, c] of [...counts].sort((a, b) => a[0] - b[0])) {
    const cx = L + (y - y0 + .5) * slot, x = cx - bw / 2, top = ys(c), base = H - B;
    const h = base - top;
    const rr = Math.min(r, h);
    svg += `<rect class="hit" x="${cx - slot / 2}" y="${T}" width="${slot}" height="${H - T - B}" data-tip="${y}: ${plural(c, "appearance")}"/>`;
    svg += `<path class="bar" d="M${x},${base}V${top + rr}Q${x},${top} ${x + rr},${top}H${x + bw - rr}Q${x + bw},${top} ${x + bw},${top + rr}V${base}Z"/>`;
  }
  const rows = [...counts].sort((a, b) => a[0] - b[0]).map(([y, c]) => `<tr><td>${y}</td><td>${c}</td></tr>`).join("");
  return `<figure class="chart">
    <figcaption class="chart-title">Appearances in scanned issues, by year</figcaption>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Appearances per year from ${Math.min(...years)} to ${Math.max(...years)}, most in one year: ${max}">${svg}</svg>
    <details><summary>Show as a table</summary>
      <table class="toc"><thead><tr><th scope="col">Year</th><th scope="col">Appearances</th></tr></thead><tbody>${rows}</tbody></table>
    </details>
  </figure>`;
}

function wireChart() {
  for (const hit of main.querySelectorAll(".chart .hit")) {
    hit.addEventListener("mousemove", e => {
      tooltip.textContent = hit.dataset.tip;
      tooltip.hidden = false;
      const x = Math.min(e.clientX + 12, window.innerWidth - tooltip.offsetWidth - 8);
      tooltip.style.left = `${x}px`;
      tooltip.style.top = `${e.clientY - tooltip.offsetHeight - 10}px`;
    });
    hit.addEventListener("mouseleave", () => { tooltip.hidden = true; });
  }
}

/* ---------- Search (ARIA combobox) ---------- */

let index = null;
let active = -1;
let results = [];

async function loadIndex() {
  if (index) return index;
  const raw = await load("search.json");
  index = raw.map(([kind, id, label, detail, weight]) => ({ kind, id, label, detail, weight, n: norm(label) }));
  return index;
}

/* 0 exact, 1 every query word starts a word of the label, 2 substring; -1 no match.
   Within a tier, bigger entries (more appearances / issues) rank first. */
function score(e, q, words) {
  if (e.n === q) return 0;
  const ws = e.n.split(" ");
  if (words.every(w => ws.some(x => x.startsWith(w)))) return 1;
  if (e.n.includes(q)) return 2;
  return -1;
}

async function suggest() {
  const q = norm(input.value);
  if (q.length < 2) { closeSuggestions(); return; }
  const idx = await loadIndex();
  if (norm(input.value) !== q) return;  // typed on while loading
  const words = q.split(" ");
  const kindRank = { a: 0, m: 1, w: 2 };
  results = [];
  for (const e of idx) {
    const s = score(e, q, words);
    if (s >= 0) results.push([s, e]);
  }
  results.sort((a, b) => a[0] - b[0] || b[1].weight - a[1].weight || kindRank[a[1].kind] - kindRank[b[1].kind] || a[1].label.length - b[1].label.length);
  results = results.slice(0, 12).map(r => r[1]);
  active = -1;
  list.innerHTML = results.map((e, i) => `<li role="option" id="opt-${i}" aria-selected="false">
      <span class="kind">${KIND[e.kind]}</span><span class="s-label">${highlight(e.label, words)}</span>
      <span class="s-detail">${esc(e.kind === "w" ? `by ${e.detail}` : e.detail)}</span></li>`).join("")
    || `<li class="empty" role="option" aria-disabled="true">No matches</li>`;
  list.hidden = false;
  input.setAttribute("aria-expanded", "true");
  statusEl.textContent = results.length ? `${results.length} suggestions` : "No matches";
  list.querySelectorAll("li[id]").forEach((li, i) => li.addEventListener("mousedown", ev => { ev.preventDefault(); go(results[i]); }));
}

function highlight(label, words) {
  const safe = esc(label);
  const re = new RegExp(`\\b(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return safe.replace(re, "<mark>$1</mark>");
}

function setActive(i) {
  active = (i + results.length) % results.length;
  list.querySelectorAll("li[id]").forEach((li, j) => li.setAttribute("aria-selected", String(j === active)));
  const el = document.getElementById(`opt-${active}`);
  input.setAttribute("aria-activedescendant", el.id);
  el.scrollIntoView({ block: "nearest" });
}

function go(e) {
  input.value = "";
  closeSuggestions();
  location.hash = e.kind === "a" ? `#/author/${e.id}` : e.kind === "w" ? `#/author/${e.id}` : `#/magazine/${encodeURIComponent(e.id)}`;
}

function closeSuggestions() {
  list.hidden = true;
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
}

let timer;
input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(suggest, 80); });
input.addEventListener("focus", () => { loadIndex().catch(() => {}); });
input.addEventListener("keydown", e => {
  if (e.key === "ArrowDown" && results.length) { e.preventDefault(); if (list.hidden) suggest(); else setActive(active + 1); }
  else if (e.key === "ArrowUp" && results.length && !list.hidden) { e.preventDefault(); setActive(active - 1); }
  else if (e.key === "Enter" && !list.hidden && results.length) { e.preventDefault(); go(results[Math.max(active, 0)]); }
  else if (e.key === "Escape") { closeSuggestions(); }
});
// Deferred so a click elsewhere settles first; skip if focus came back (e.g. a "Try" chip refocused the input).
input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeSuggestions(); }, 100));

route();
