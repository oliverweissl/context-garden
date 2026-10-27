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
