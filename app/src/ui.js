/** h("div", {class: "x", onclick: fn}, child, "text", [more]) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

let toastTimer;
export function toast(text, kind = "info") {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), kind === "error" ? 6000 : 3000);
}

export function setCrumbs(...parts) {
  const nav = document.getElementById("crumbs");
  nav.replaceChildren(...parts.flatMap((p) => [h("span", { class: "sep" }, "/"), p]));
}

export function fmt(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e4) return (n / 1e3).toFixed(0) + "k";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

/** Stat tile; `share` (0..1) adds a thin meter under the value. */
export function tile(label, value, note, share = null) {
  return h("div", { class: "tile" },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value),
    share == null ? null : h("span", { class: "meter" }, h("span", { style: `width:${Math.round(100 * Math.min(1, share))}%` })),
    note ? h("div", { class: "tile-note" }, note) : null);
}

/** Categorical series colors, validated for the dark surface (fixed order; 7th+ fold into "other"). */
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"];

/** Share of lines per language as one stacked bar with a legend. */
export function languageBar(languages) {
  const all = Object.entries(languages).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const total = all.reduce((t, [, n]) => t + n, 0);
  if (!total) return h("div");
  const top = all.slice(0, SERIES.length);
  const rest = all.slice(SERIES.length).reduce((t, [, n]) => t + n, 0);
  const parts = top.map(([ext, n], i) => [ext === "other" ? "no extension" : `.${ext}`, n, SERIES[i]]);
  if (rest) parts.push(["other", rest, "var(--faint)"]);
  const pct = (n) => (100 * n) / total;
  return h("div", { class: "langs" },
    h("div", { class: "stack" }, parts.map(([name, n, c]) =>
      h("span", { style: `flex:${n};background:${c}`, title: `${name}: ${fmt(n)} lines (${pct(n).toFixed(1)}%)` }))),
    h("div", { class: "legend small" }, parts.map(([name, n, c]) =>
      h("span", { title: `${fmt(n)} lines` }, h("i", { style: `background:${c}` }), name,
        h("span", { class: "faint" }, ` ${pct(n) < 1 ? "<1" : Math.round(pct(n))}%`)))));
}

/** Everything an account read and wrote: fresh input, cache and output tokens. */
export const accountTotal = (a) => a.input + a.cache_read + a.cache_write + a.output;

/**
 * Token use split by account as a donut with a legend. Each slice wears the
 * account's own badge color (the one on its terminals); the default config
 * dirs, which have none, take series colors. The legend repeats the badge.
 */
export function tokenDonut(accounts, days) {
  const rows = accounts.map((a) => ({ a, n: accountTotal(a) })).filter((r) => r.n > 0).sort((x, y) => y.n - x.n);
  const total = rows.reduce((t, r) => t + r.n, 0);
  if (!total) {
    return h("div", { class: "tile" },
      h("div", { class: "tile-label" }, "Tokens by account"),
      h("div", { class: "tile-value faint" }, "–"),
      h("div", { class: "tile-note" }, "No stats yet: no agent has run in this garden"));
  }
  // Spare colors for the default dirs: series colors not close to any account's.
  const rgb = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const near = (x, y) => Math.hypot(...rgb(x).map((v, i) => v - rgb(y)[i])) < 90;
  const used = rows.map((r) => r.a.color).filter((c) => /^#[0-9a-f]{6}$/i.test(c));
  const spare = SERIES.filter((c) => !used.some((u) => near(c, u)));
  rows.forEach((r) => {
    if (r.a.color) r.color = r.a.color;
    else { r.color = spare.shift() || "#9ba596"; used.push(r.color); }
  });
  const R = 38, C = 2 * Math.PI * R, GAP = rows.length > 1 ? 2 : 0;
  let at = 0;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("class", "donut");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Tokens by account, ${fmt(total)} in all`);
  for (const r of rows) {
    const len = (C * r.n) / total;
    const arc = document.createElementNS(NS, "circle");
    for (const [k, v] of Object.entries({
      cx: 50, cy: 50, r: R, fill: "none", stroke: r.color, "stroke-width": 14,
      "stroke-dasharray": `${Math.max(0.5, len - GAP)} ${C}`, "stroke-dashoffset": -at, transform: "rotate(-90 50 50)",
    })) arc.setAttribute(k, v);
    const tip = document.createElementNS(NS, "title");
    tip.textContent = `${r.a.name}: ${fmt(r.n)} tokens (${((100 * r.n) / total).toFixed(1)}%) · ${r.a.sessions} sessions`;
    arc.append(tip);
    svg.append(arc);
    at += len;
  }
  const label = (y, text, cls) => {
    const t = document.createElementNS(NS, "text");
    for (const [k, v] of Object.entries({ x: 50, y, "text-anchor": "middle", class: cls })) t.setAttribute(k, v);
    t.textContent = text;
    svg.append(t);
  };
  label(52, fmt(total), "donut-value");
  label(64, "tokens", "donut-note");
  const sessions = rows.reduce((t, r) => t + r.a.sessions, 0);
  const read = rows.reduce((t, r) => t + r.a.input + r.a.cache_read + r.a.cache_write, 0);
  const cache = read ? Math.round((100 * rows.reduce((t, r) => t + r.a.cache_read, 0)) / read) : 0;
  const pct = (n) => { const v = (100 * n) / total; return v < 1 ? "<1%" : `${Math.round(v)}%`; };
  return h("div", { class: "tile token-split" },
    h("div", { class: "tile-label" }, "Tokens by account"),
    h("div", { class: "token-body" }, svg,
      h("div", { class: "token-legend small" }, rows.map((r) =>
        h("div", { class: "token-row", title: `${fmt(r.a.output)} out · ${fmt(r.a.cache_read)} from cache` },
          h("i", { style: `background:${r.color}` }),
          r.a.icon ? badge(r.a) : null,
          h("span", { class: "ellipsis" }, r.a.name, h("span", { class: "faint" }, ` · ${r.a.agent}`)),
          h("span", { class: "grow" }),
          h("b", {}, fmt(r.n)),
          h("span", { class: "faint pct" }, pct(r.n)))))),
    h("div", { class: "tile-note" }, `Claude Code + Codex in this garden · ${sessions} sessions · last ${days} days · ${cache}% from cache`));
}

export const SKILL_COLORS = {
  greenhouse: "var(--sky)", mycelium: "var(--violet)", seedbank: "var(--amber)",
  pruner: "var(--rose)", compost: "var(--compost)", weeder: "var(--weeder)",
};

export const SKILL_BLURBS = {
  greenhouse: "Shared memory across the repos of one platform",
  mycelium: "Briefs subagents and passes their findings on",
  seedbank: "Small AGENTS.md of facts agents keep rediscovering",
  pruner: "Smallest file:line slice for a task instead of whole files",
  compost: "Clusters long build/test output, shows only what changed",
  weeder: "Audits and slims Skills without losing instructions",
};

export function errText(e) {
  return typeof e === "string" ? e : e?.message || JSON.stringify(e);
}

/** Account badge: its symbol on its color (falls back for accounts made before icons). */
export function badge(profile, big = false) {
  const color = profile.color || "#9ba596";
  return h("span", { class: `badge${big ? " big" : ""}`, style: `--c:${color}`, title: profile.name || "" },
    profile.icon || (profile.name || "?").slice(0, 1).toUpperCase());
}

/**
 * Popup window over the page. Esc, the × or a click outside closes it.
 * Returns { el, body, close }; `onClose` runs once it is gone.
 */
export function modal(title, content, { onClose, wide = false } = {}) {
  const body = h("div", { class: "modal-body" }, content);
  const dialog = h("dialog", { class: `modal${wide ? " wide" : ""}` },
    h("div", { class: "modal-head" },
      h("h3", {}, title),
      h("button", { class: "icon", title: "Close", onclick: () => dialog.close() }, "×")),
    body);
  // A click on the backdrop lands on the dialog itself, outside its box.
  dialog.addEventListener("click", (e) => {
    const r = dialog.getBoundingClientRect();
    if (e.target === dialog && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dialog.close();
  });
  dialog.addEventListener("close", () => { dialog.remove(); onClose?.(); });
  document.body.append(dialog);
  dialog.showModal();
  return { el: dialog, body, close: () => dialog.close() };
}
