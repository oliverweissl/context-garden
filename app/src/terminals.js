import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { api } from "./api.js";
import { h, toast, errText, badge, modal } from "./ui.js";

const THEME = {
  background: "#040604", foreground: "#d9f2dc", cursor: "#46d872", cursorAccent: "#040604",
  selectionBackground: "rgba(70, 216, 114, 0.28)",
  black: "#0a0e0a", red: "#ff7a8f", green: "#46d872", yellow: "#f0b43c",
  blue: "#5bb0ff", magenta: "#a68cff", cyan: "#3fd6c6", white: "#f1f4ee",
  brightBlack: "#636c60", brightRed: "#ff9aab", brightGreen: "#7ce69b", brightYellow: "#f5cb6e",
  brightBlue: "#86c5ff", brightMagenta: "#c2b0ff", brightCyan: "#72e6da", brightWhite: "#ffffff",
};

const now = () => Math.floor(Date.now() / 1000);

/** Layout tree: a leaf { pane } or a split { dir: "row" | "col", kids, sizes } (sizes are flex weights). */
const SIDES = { left: ["row", true], right: ["row", false], top: ["col", true], bottom: ["col", false] };
const sum = (xs) => xs.reduce((t, x) => t + x, 0);
const MIN_PANE = 140; // px a splitter leaves each side

/**
 * Terminal workspace of one garden. Lives across navigation (terminals keep
 * running while you look at another garden). What runs in each pane is saved
 * with the garden, so after a restart (or closing one) it can be resumed;
 * Claude panes get their own session id and resume exactly that conversation.
 * Panes tile the area: drag a header onto a pane's edge to split it there,
 * drag the gaps between panes to resize.
 */
class Workspace {
  constructor(gardenId, saved = []) {
    this.gardenId = gardenId;
    /** Saved records of panes that are not live: open at last quit, or closed. */
    this.saved = saved;
    this.onChange = () => {};
    this.panes = [];
    this.root = null;
    this.grid = h("div", { class: "panes" });
    this.resizeObserver = new ResizeObserver(() => this.fitAll());
    this.resizeObserver.observe(this.grid);
    this.ticks = 0;
    this.poller = gardenId ? setInterval(() => this.poll(), 5000) : null;
  }

  fitAll() {
    requestAnimationFrame(() => this.panes.forEach((p) => p.fit()));
  }

  render() {
    this.grid.replaceChildren(...(this.root ? [this.node(this.root)] : []));
    this.fitAll();
  }

  node(n) {
    if (n.pane) return n.pane.el;
    const box = h("div", { class: `split split-${n.dir}` });
    n.kids.forEach((k, i) => {
      if (i) box.append(this.splitter(n, i, box));
      box.append(h("div", { class: "cell", style: `flex:${n.sizes[i]} 1 0px` }, this.node(k)));
    });
    return box;
  }

  /** The gap between kid i-1 and kid i of `n`: drag to move it. */
  splitter(n, i, box) {
    return h("div", {
      class: `splitter splitter-${n.dir}`,
      onpointerdown: (down) => {
        if (down.button !== 0) return;
        down.preventDefault();
        const cells = [...box.children].filter((c) => c.classList.contains("cell"));
        const r = box.getBoundingClientRect();
        const length = n.dir === "row" ? r.width : r.height;
        const at = (e) => (n.dir === "row" ? e.clientX : e.clientY);
        const start = at(down), a0 = n.sizes[i - 1], b0 = n.sizes[i];
        const total = sum(n.sizes);
        const min = Math.min((a0 + b0) / 2, (total * MIN_PANE) / length);
        document.body.classList.add(n.dir === "row" ? "resizing-x" : "resizing-y");
        const move = (e) => {
          const a = Math.max(min, Math.min(a0 + b0 - min, a0 + ((at(e) - start) / length) * total));
          n.sizes[i - 1] = a;
          n.sizes[i] = a0 + b0 - a;
          cells[i - 1].style.flexGrow = a;
          cells[i].style.flexGrow = n.sizes[i];
        };
        const up = () => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          document.body.classList.remove("resizing-x", "resizing-y");
          this.saveLayout();
          this.fitAll();
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
      },
    });
  }

  /** { node, parent } of the leaf holding `pane`. */
  find(pane, n = this.root, parent = null) {
    if (!n) return null;
    if (n.pane) return n.pane === pane ? { node: n, parent } : null;
    for (const k of n.kids) {
      const f = this.find(pane, k, n);
      if (f) return f;
    }
    return null;
  }

  parentOf(target, n = this.root) {
    if (!n || n.pane) return null;
    if (n.kids.includes(target)) return n;
    for (const k of n.kids) {
      const p = this.parentOf(target, k);
      if (p) return p;
    }
    return null;
  }

  /** Put `neu` where `old` is; a split in the same direction as its new parent merges into it. */
  replace(old, neu) {
    if (this.root === old) {
      this.root = neu;
      return;
    }
    const parent = this.parentOf(old);
    const i = parent.kids.indexOf(old);
    if (!neu.pane && neu.dir === parent.dir) {
      const share = parent.sizes[i], total = sum(neu.sizes);
      parent.kids.splice(i, 1, ...neu.kids);
      parent.sizes.splice(i, 1, ...neu.sizes.map((x) => (x * share) / total));
    } else {
      parent.kids[i] = neu;
    }
  }

  /** Take `pane` out of the tree; its neighbour gets the space. */
  detach(pane) {
    const f = this.find(pane);
    if (!f) return;
    if (!f.parent) {
      this.root = null;
      return;
    }
    const p = f.parent, i = p.kids.indexOf(f.node);
    const share = p.sizes[i];
    p.kids.splice(i, 1);
    p.sizes.splice(i, 1);
    p.sizes[Math.max(0, i - 1)] += share;
    if (p.kids.length === 1) this.replace(p, p.kids[0]);
  }

  /** Split `target`'s place: `pane` goes to its left, right, top or bottom. */
  insert(target, pane, side) {
    const leaf = { pane };
    if (!this.root) {
      this.root = leaf;
      return;
    }
    const [dir, before] = SIDES[side];
    const t = this.find(target);
    const p = t.parent;
    if (p && p.dir === dir) {
      const i = p.kids.indexOf(t.node), half = p.sizes[i] / 2;
      p.sizes[i] = half;
      p.kids.splice(before ? i : i + 1, 0, leaf);
      p.sizes.splice(before ? i : i + 1, 0, half);
    } else {
      this.replace(t.node, { dir, kids: before ? [leaf, t.node] : [t.node, leaf], sizes: [1, 1] });
    }
  }

  /** A new pane halves the biggest one: side by side if it is wide, else below it. */
  place(pane) {
    let best = null, area = -1;
    for (const p of this.panes) {
      if (p === pane) continue;
      const r = p.el.getBoundingClientRect();
      if (r.width * r.height > area) [best, area] = [{ p, r }, r.width * r.height];
    }
    if (!best) return this.insert(null, pane, "right");
    this.insert(best.p, pane, !area || best.r.width > best.r.height * 1.4 ? "right" : "bottom");
  }

  /** Rebuild the tree from pane order: all side by side, all stacked, or a grid. */
  arrange(kind) {
    const leaves = this.panes.map((pane) => ({ pane }));
    const line = (dir, kids) => (kids.length === 1 ? kids[0] : { dir, kids, sizes: kids.map(() => 1) });
    if (!leaves.length) this.root = null;
    else if (kind === "columns") this.root = line("row", leaves);
    else if (kind === "rows") this.root = line("col", leaves);
    else {
      const per = Math.ceil(Math.sqrt(leaves.length));
      const rows = [];
      for (let i = 0; i < leaves.length; i += per) rows.push(line("row", leaves.slice(i, i + per)));
      this.root = line("col", rows);
    }
    this.render();
    this.saveLayout();
  }

  get layoutKey() { return this.gardenId ? `cg.layout.${this.gardenId}` : null; }

  saveLayout() {
    const shape = (n) => (n.pane ? n.pane.opts.key : { dir: n.dir, sizes: n.sizes, kids: n.kids.map(shape) });
    try { if (this.layoutKey) localStorage.setItem(this.layoutKey, JSON.stringify(this.root ? shape(this.root) : null)); } catch { /* private mode */ }
  }

  /** The tiling saved last time, if it holds exactly the panes open now. */
  restoreLayout() {
    let shape = null;
    try { shape = JSON.parse(localStorage.getItem(this.layoutKey) || "null"); } catch { return; }
    const keys = [];
    const walk = (s) => (typeof s === "string" ? keys.push(s) : s?.kids?.forEach(walk));
    walk(shape);
    const byKey = new Map(this.panes.map((p) => [p.opts.key, p]));
    if (!shape || keys.length !== byKey.size || !keys.every((k) => byKey.has(k))) return;
    const build = (s) => (typeof s === "string" ? { pane: byKey.get(s) } : { dir: s.dir, sizes: [...s.sizes], kids: s.kids.map(build) });
    this.root = build(shape);
    this.render();
  }

  /** Open a pane. `opts.resume` reopens a saved one (same key and session). */
  async open(opts) {
    opts.key ||= crypto.randomUUID();
    const claude = opts.agent === "claude" || opts.account?.agent === "claude";
    if (claude && !opts.session) opts.session = crypto.randomUUID();
    this.saved = this.saved.filter((r) => r.key !== opts.key);
    const pane = new Pane(this, opts);
    this.place(pane);
    this.panes.push(pane);
    this.render();
    this.persist();
    await pane.start();
    this.fitAll();
  }

  remove(pane) {
    this.panes = this.panes.filter((p) => p !== pane);
    this.detach(pane);
    if (pane.el.classList.contains("max")) this.grid.classList.remove("has-max");
    this.render();
    this.saved.unshift({ ...record(pane.opts), open: false, used_at: now() });
    this.persist();
    this.saveLayout();
  }

  /** Forget that saved panes were open (they stay under "Resume"). */
  dismiss() {
    this.saved = this.saved.map((r) => ({ ...r, open: false }));
    this.persist();
  }

  /** Drop one saved pane from the list. */
  forget(key) {
    this.saved = this.saved.filter((r) => r.key !== key);
    this.persist();
  }

  /** Drop closed panes from the list. Their conversations stay in the agent's own history. */
  forgetClosed() {
    this.saved = this.saved.filter((r) => r.open);
    this.persist();
  }

  persist() {
    this.onChange();
    if (!this.gardenId) return;
    const live = this.panes.map((p) => ({ ...record(p.opts), open: true, used_at: now() }));
    api.saveTerminals(this.gardenId, [...live, ...this.saved]).catch(() => {});
  }

  /**
   * Every 5 s: where each live agent works now; every 30 s: whether its
   * account got newer plugins than the ones it started with.
   */
  async poll() {
    const agents = this.panes.filter((p) => p.id != null && p.agent);
    if (!agents.length) return;
    const refs = agents.map((p) => ({
      key: p.opts.key, agent: p.opts.agent || null, profile: p.opts.profile || null,
      session: p.opts.session || null, cwd: p.opts.cwd, started: p.started,
    }));
    api.terminalLocations(this.gardenId, refs)
      .then((at) => agents.forEach((p) => p.showLocation(at[p.opts.key])))
      .catch(() => {});
    if (this.ticks++ % 6) return;
    const byAccount = new Map();
    for (const p of agents) {
      const k = p.opts.profile || `default:${p.agent}`;
      byAccount.set(k, [...(byAccount.get(k) || []), p]);
    }
    for (const panes of byAccount.values()) {
      const { agent, profile } = panes[0].opts;
      api.accountPlugins(agent, profile).then((now) => panes.forEach((p) => p.checkPlugins(now))).catch(() => {});
    }
  }

  /**
   * Rearrange by dragging a pane's header: the pane follows the pointer and
   * drops on the side of the pane under it that is nearest the pointer.
   * Pointer events, not HTML5 drag and drop, which the desktop window
   * reserves for dropping files.
   */
  drag(pane, down) {
    if (down.button !== 0 || down.target.closest("button") || this.panes.length < 2) return;
    const start = { x: down.clientX, y: down.clientY };
    let lifted = false;
    let target = null;
    const DROPS = Object.keys(SIDES).map((s) => `drop-${s}`);
    const clear = () => this.panes.forEach((p) => p.el.classList.remove(...DROPS));
    const move = (e) => {
      const dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (!lifted) {
        if (Math.hypot(dx, dy) < 6) return;
        lifted = true;
        pane.el.classList.add("lifted");
        document.body.classList.add("dragging-pane");
      }
      pane.el.style.transform = `translate(${dx}px, ${dy}px)`;
      clear();
      const over = this.panes.find((p) => p !== pane && inside(p.el.getBoundingClientRect(), e));
      target = null;
      if (!over) return;
      const r = over.el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      const side = Object.entries({ left: x, right: 1 - x, top: y, bottom: 1 - y }).sort((a, b) => a[1] - b[1])[0][0];
      over.el.classList.add(`drop-${side}`);
      target = { over, side };
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("dragging-pane");
      pane.el.classList.remove("lifted");
      pane.el.style.transform = "";
      clear();
      if (!target) return;
      this.detach(pane);
      this.insert(target.over, pane, target.side);
      this.render();
      this.saveLayout();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }
}

const inside = (r, e) => e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;

/** What to save about a pane to start it again. */
const record = (o) => ({
  key: o.key, title: o.title, cwd: o.cwd, cwd_label: o.cwdLabel || "",
  agent: o.agent || null, profile: o.profile || null, session: o.session || null, flags: o.flags || [],
});

/** Split a flags line like a shell would: spaces separate, quotes group. */
export function splitFlags(line) {
  const out = [];
  for (const m of line.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

const QUICK_FLAGS = { claude: [["--chrome", "Claude in Chrome"], ["--ide", "Connect to your IDE"]], codex: [["--search", "Web search"]] };
const FLAGS_KEY = (agent) => `cg.flags.${agent}`;
const loadFlags = (agent) => { try { return localStorage.getItem(FLAGS_KEY(agent)) || ""; } catch { return ""; } };
const storeFlags = (agent, v) => { try { localStorage.setItem(FLAGS_KEY(agent), v); } catch { /* private mode */ } };

const MAC = /Mac|iPhone|iPad/.test(navigator.platform);

/**
 * Copy and paste for a terminal: Cmd+C / Cmd+V on macOS; Ctrl+Shift+C / V
 * elsewhere, plus Ctrl+C while text is selected and Ctrl+V. Ctrl+C without a
 * selection still interrupts. Returns false for keys handled here.
 */
function clipboardKey(term, e) {
  if (e.type !== "keydown" || e.altKey) return true;
  const key = e.key.toLowerCase();
  const mod = MAC ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  if (!mod || (key !== "c" && key !== "v")) return true;
  if (key === "c") {
    if (!term.hasSelection()) return !MAC && !e.shiftKey; // plain Ctrl+C: interrupt
    e.preventDefault();
    api.copy(term.getSelection()).then(() => term.clearSelection(), (err) => toast(errText(err), "error"));
    return false;
  }
  e.preventDefault(); // no second, native paste
  api.pasteText().then((text) => text && term.paste(text), (err) => toast(errText(err), "error"));
  return false;
}

/** What agents print when the account has no usage left (not "context limit"). */
const OUT_OF_CREDITS = /(?:usage|session|weekly|5-hour) limit (?:reached|hit)|hit your (?:usage )?limit|usage limit|credit balance is too low|out of (?:credits|extra usage)|insufficient_quota|exceeded your current quota/i;
// Escape sequences become spaces: TUIs move the cursor between the words of one message.
const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

class Pane {
  constructor(ws, opts) {
    this.ws = ws;
    this.opts = opts;
    this.id = null;
    this.agent = opts.login ? null : opts.account?.agent || opts.agent || null;
    this.started = now();
    /** Plugin stamps the agent started with, by plugin id (null until known). */
    this.stamps = null;
    this.state = { live: true, stale: "", noCredits: false };
    this.recent = "";
    this.decoder = new TextDecoder();
    this.term = new Terminal({
      theme: THEME, fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      fontSize: 13, cursorBlink: true, allowProposedApi: false, scrollback: 5000,
      // Agents that capture the mouse still let Option-drag (macOS) / Shift-drag select.
      macOptionClickForcesSelection: true,
    });
    this.term.attachCustomKeyEventHandler((e) => this.key(e));
    this.fitter = new FitAddon();
    this.term.loadAddon(this.fitter);
    const body = h("div", { class: "pane-body" });
    this.status = h("span", { class: "pane-status live" });
    this.location = h("span", { class: "pane-at small ellipsis", hidden: true });
    this.el = h("section", { class: "pane" },
      h("header", {
        class: "pane-head", title: "Drag onto another terminal's edge to put it there",
        onpointerdown: (e) => ws.drag(this, e),
      },
      this.status,
      opts.account ? badge(opts.account) : null,
      h("b", {}, opts.title),
      h("span", { class: "faint small ellipsis", title: "Started in" }, opts.cwdLabel),
      this.location,
      h("span", { class: "grow" }),
      h("button", { class: "icon", title: "Maximize", onclick: () => this.toggleMax() }, "⤢"),
      h("button", { class: "icon", title: "Close", onclick: () => this.close() }, "×")),
      body);
    this.term.open(body);
    this.term.onData((d) => {
      if (this.id == null) return;
      if (d.includes("\r") && this.state.noCredits) this.mark({ noCredits: false });
      if (d.includes("\r")) this.recent = "";
      api.write(this.id, d).catch(() => {});
    });
    this.term.onResize(({ cols, rows }) => this.id != null && api.resize(this.id, cols, rows).catch(() => {}));
  }

  /** Shift+Enter is a line break (Ctrl+J, which Claude Code and Codex read as one); copy and paste. */
  key(e) {
    if (e.key === "Enter" && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (e.type === "keydown" && this.id != null) api.write(this.id, "\n").catch(() => {});
      e.preventDefault();
      return false;
    }
    return clipboardKey(this.term, e);
  }

  async start() {
    this.fitter.fit();
    try {
      const size = { cols: this.term.cols, rows: this.term.rows };
      const out = (bytes) => {
        this.term.write(bytes);
        if (this.agent) this.scan(this.decoder.decode(bytes, { stream: true }));
      };
      const exit = (code) => this.exited(code);
      this.started = now();
      this.id = this.opts.login
        ? await api.login({ profile: this.opts.login, ...size }, out, exit)
        : await api.spawn(
          { garden: this.ws.gardenId, cwd: this.opts.cwd, agent: this.opts.agent, profile: this.opts.profile,
            session: this.opts.session, resume: !!this.opts.resume, flags: this.opts.flags, ...size },
          out, exit);
      this.term.focus();
      if (this.agent && this.ws.gardenId) {
        api.accountPlugins(this.opts.agent, this.opts.profile)
          .then((list) => { this.stamps = Object.fromEntries(list.map((x) => [x.id, x])); })
          .catch(() => {});
      }
    } catch (e) {
      this.term.write(`\x1b[31m${errText(e)}\x1b[0m\r\n`);
      this.exited(null);
    }
  }

  scan(text) {
    this.recent = (this.recent + text.replace(ANSI, " ").replace(/\s+/g, " ")).slice(-4000);
    if (!this.state.noCredits && OUT_OF_CREDITS.test(this.recent)) this.mark({ noCredits: true });
  }

  /** Yellow when the account's plugins changed after this agent started: it still runs the old ones. */
  checkPlugins(list) {
    if (!this.stamps) return;
    const changed = list.filter((x) => this.stamps[x.id]?.stamp !== x.stamp).map((x) => {
      const was = this.stamps[x.id];
      const name = x.id.split("@")[0];
      return was ? `${name} ${was.version} → ${x.version}` : `${name} ${x.version} (new)`;
    });
    this.mark({ stale: changed.length ? `Runs older plugins than installed: ${changed.join(", ")}. Close and resume to load them.` : "" });
  }

  showLocation(label) {
    const show = !!label && label !== this.opts.cwdLabel;
    this.location.hidden = !show;
    this.location.textContent = show ? `→ ${label}` : "";
    this.location.title = show ? `Working in ${label} now` : "";
  }

  mark(change) {
    Object.assign(this.state, change);
    const { live, stale, noCredits } = this.state;
    const [cls, tip] = noCredits ? ["out", "Out of credits: the agent reports its usage limit"]
      : !live ? ["", "Exited"]
        : stale ? ["stale", stale]
          : ["live", "Running"];
    this.status.className = `pane-status ${cls}`;
    this.status.title = tip;
  }

  exited(code) {
    this.id = null;
    this.mark({ live: false });
    this.location.hidden = true;
    this.term.write(`\r\n\x1b[90m[process exited${code != null ? ` with ${code}` : ""}]\x1b[0m\r\n`);
  }

  fit() {
    if (this.el.isConnected && this.el.offsetWidth) {
      try { this.fitter.fit(); } catch { /* not laid out yet */ }
    }
  }

  toggleMax() {
    this.el.classList.toggle("max");
    this.ws.grid.classList.toggle("has-max", this.ws.panes.some((p) => p.el.classList.contains("max")));
    this.ws.fitAll();
  }

  close() {
    if (this.id != null) api.kill(this.id);
    this.term.dispose();
    this.ws.remove(this);
  }
}

/**
 * One terminal that signs an account in: the agent CLI runs in the account's
 * config dir and opens the browser login itself. Gone when closed.
 */
export async function loginTerminal(profile, container) {
  const ws = new Workspace(null);
  container.replaceChildren(ws.grid); // attached first so the terminal can measure itself
  await ws.open({ login: profile.id, account: profile, title: `Sign in · ${profile.name}`, cwdLabel: "" });
  return { close: () => [...ws.panes].forEach((p) => p.close()) };
}

/**
 * Where a terminal starts: the garden's folders as a tree (root, group
 * folders, repos), with a filter. Exposes `value` (path) and `label`.
 */
function folderPicker(garden) {
  const rootNode = { name: "garden root", path: garden.root, kids: new Map(), repo: false };
  const ready = garden.repos.filter((r) => ["cloned", "linked", "local"].includes(r.status));
  const prefix = garden.root.replace(/\/+$/, "") + "/";
  for (const r of ready) {
    // Repos outside the root (linked folders) hang off the root by name.
    const parts = r.path.startsWith(prefix) ? r.path.slice(prefix.length).split("/") : [r.name];
    let node = rootNode;
    parts.forEach((part, i) => {
      const path = r.path.startsWith(prefix) ? prefix + parts.slice(0, i + 1).join("/") : r.path;
      if (!node.kids.has(part)) node.kids.set(part, { name: part, path, kids: new Map(), repo: false });
      node = node.kids.get(part);
    });
    Object.assign(node, { repo: true, label: r.name });
  }
  const labelOf = (n) => (n === rootNode ? "garden root" : n.label || n.path.slice(prefix.length));
  const picker = { value: garden.root, label: "garden root" };
  const button = h("button", { class: "btn picker-btn", title: "Folder the terminal starts in" });
  const filter = h("input", { class: "picker-filter", placeholder: "filter repositories…", spellcheck: false });
  const list = h("div", { class: "picker-list" });
  const pop = h("div", { class: "picker-pop", hidden: true }, filter, list);
  const closed = new Set();
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    const matches = (n) => !q || n.name.toLowerCase().includes(q) || [...n.kids.values()].some(matches);
    const rows = [];
    const walk = (n, depth) => {
      if (!matches(n)) return;
      const kids = [...n.kids.values()].sort((a, b) => (a.repo - b.repo) || a.name.localeCompare(b.name));
      const open = q || !closed.has(n.path);
      rows.push(h("div", {
        class: `picker-row${n.path === picker.value ? " active" : ""}${n.repo ? " repo" : ""}`,
        style: `padding-left:${8 + depth * 16}px`,
        onclick: () => choose(n),
      },
      kids.length ? h("span", {
        class: "caret",
        onclick: (e) => { e.stopPropagation(); closed.has(n.path) ? closed.delete(n.path) : closed.add(n.path); draw(); },
      }, open ? "▾" : "▸") : h("span", { class: "caret" }),
      h("span", { class: "ellipsis" }, n === rootNode ? "garden root" : n.name),
      kids.length ? h("span", { class: "faint small" }, ` ${countRepos(n)}`) : null));
      if (open) kids.forEach((k) => walk(k, depth + 1));
    };
    walk(rootNode, 0);
    list.replaceChildren(...rows);
  };
  const countRepos = (n) => [...n.kids.values()].reduce((t, k) => t + (k.repo ? 1 : 0) + countRepos(k), 0);
  const choose = (n) => {
    picker.value = n.path;
    picker.label = labelOf(n);
    button.textContent = `📁 ${picker.label}`;
    hide();
  };
  const outside = (e) => { if (!picker.el.contains(e.target)) hide(); };
  const hide = () => { pop.hidden = true; document.removeEventListener("pointerdown", outside); };
  button.onclick = () => {
    if (!pop.hidden) return hide();
    pop.hidden = false;
    filter.value = "";
    draw();
    filter.focus();
    document.addEventListener("pointerdown", outside);
  };
  filter.addEventListener("input", draw);
  filter.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
    if (e.key === "Enter") (list.querySelector(".picker-row.repo") || list.querySelector(".picker-row"))?.click();
  });
  button.textContent = `📁 ${picker.label}`;
  picker.el = h("div", { class: "picker" }, button, pop);
  return picker;
}

const workspaces = new Map();

/** Toolbar + panes for a garden. Returns the element; terminals survive re-render. */
export async function terminalArea(garden) {
  let ws = workspaces.get(garden.id);
  if (!ws) workspaces.set(garden.id, (ws = new Workspace(garden.id, garden.terminals || [])));

  const [agents, profiles] = await Promise.all([api.agents(), api.profiles()]);
  const what = h("select", {},
    h("option", { value: "shell" }, "Terminal (no agent)"),
    agents.map((a) => h("option", { value: `agent:${a.id}`, disabled: !a.found },
      `${a.label}${a.found ? "" : " (not installed)"}`)),
    profiles.length
      ? h("optgroup", { label: "Accounts" }, profiles.map((p) => {
        const a = agents.find((x) => x.id === p.agent);
        return h("option", { value: `profile:${p.id}`, disabled: a && !a.found }, `${p.icon || "●"} ${a?.label || p.agent} · ${p.name}`);
      }))
      : null);
  // Startup flags for the chosen agent (e.g. --chrome), remembered per agent.
  const agentOf = () => {
    const [kind, id] = what.value.split(":");
    return kind === "agent" ? id : kind === "profile" ? profiles.find((p) => p.id === id)?.agent : null;
  };
  const flags = h("input", {
    class: "mono flags", placeholder: "flags, e.g. --model opus",
    title: "Startup flags for the agent, as on the command line. Resuming keeps them.",
  });
  const quick = h("span", { class: "quick-flags" });
  const drawFlags = () => {
    const agent = agentOf();
    flags.hidden = !agent;
    flags.value = agent ? loadFlags(agent) : "";
    const on = splitFlags(flags.value);
    quick.replaceChildren(...(QUICK_FLAGS[agent] || []).map(([flag, help]) => h("button", {
      class: `chip toggle${on.includes(flag) ? " on" : ""}`, title: help,
      onclick: () => {
        const now = splitFlags(flags.value);
        flags.value = (now.includes(flag) ? now.filter((f) => f !== flag) : [...now, flag]).join(" ");
        storeFlags(agent, flags.value);
        drawFlags();
      },
    }, flag.replace(/^--/, ""))));
  };
  flags.addEventListener("change", () => { const a = agentOf(); if (a) storeFlags(a, flags.value.trim()); drawFlags(); });
  what.addEventListener("change", drawFlags);

  const where = folderPicker(garden);
  const layout = h("div", { class: "seg", title: "Rearrange all terminals" }, ["grid", "columns", "rows"].map((l) =>
    h("button", { onclick: () => ws.arrange(l) }, l)));

  const open = async () => {
    const [kind, id] = what.value.split(":");
    if (kind === "profile" && !(await api.pluginVersion(id).catch(() => true))) {
      toast("This account has no context-garden skills yet: Settings → Accounts → Install", "error");
    }
    const label = what.selectedOptions[0].textContent.replace(" (not installed)", "");
    try {
      await ws.open({
        cwd: where.value, cwdLabel: where.label,
        agent: kind === "agent" ? id : null, profile: kind === "profile" ? id : null,
        account: kind === "profile" ? profiles.find((p) => p.id === id) : null,
        title: kind === "shell" ? "Terminal" : kind === "profile" ? label.replace(/^\S+\s/, "") : label,
        flags: kind === "shell" ? [] : splitFlags(flags.value),
      });
    } catch (e) {
      toast(errText(e), "error");
    }
  };

  // Resuming: everything that was open at the last quit, or one recent pane.
  const fromRecord = (r) => ({
    key: r.key, session: r.session, resume: true, title: r.title, cwd: r.cwd, cwdLabel: r.cwd_label,
    agent: r.agent, profile: r.profile, account: r.profile ? profiles.find((p) => p.id === r.profile) : null,
    flags: r.flags || [],
  });
  const resume = async (records) => {
    for (const r of records) {
      try {
        await ws.open(fromRecord(r));
      } catch (e) {
        toast(errText(e), "error");
      }
    }
    ws.restoreLayout();
  };
  const ago = (t) => {
    const m = Math.round((now() - t) / 60);
    return m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  };
  // Closed terminals: a button that opens a list; each row reopens or forgets one.
  const recent = h("button", { class: "btn resume-pick", title: "Terminals you closed in this garden", onclick: () => showRecent() });
  function showRecent() {
    const list = h("div", { class: "recent-list" });
    const m = modal("Closed terminals", h("div", {},
      h("p", { class: "muted small" },
        "Terminals you closed in this garden. Resume opens one again in the same folder and account; ",
        "a Claude conversation continues where it stopped (Codex continues its latest one). ",
        "Forgetting only removes it from this list."),
      list), { wide: true });
    let previews = {};
    const draw = () => {
      const closed = ws.saved.filter((r) => !r.open);
      if (!closed.length) return m.close();
      list.replaceChildren(...closed.map((r) => {
        const account = r.profile ? profiles.find((p) => p.id === r.profile) : null;
        return h("div", { class: "recent-row" },
          account ? badge(account) : null,
          h("div", { class: "grow recent-what" },
            h("div", {}, h("b", {}, r.title), h("span", { class: "faint small" }, ` · ${r.cwd_label || "garden root"} · ${ago(r.used_at)}`)),
            h("div", { class: "faint small ellipsis" }, previews[r.key] ? `“${previews[r.key]}”` : r.session ? "no messages yet" : "")),
          h("button", { class: "btn primary", onclick: () => { m.close(); resume([r]); } }, "Resume"),
          h("button", { class: "icon", title: "Forget this one", onclick: () => { ws.forget(r.key); draw(); } }, "×"));
      }),
      h("div", { class: "row end" },
        h("button", { class: "btn ghost", onclick: () => { ws.forgetClosed(); m.close(); } }, "Forget all")));
    };
    draw();
    api.terminalPreviews(ws.gardenId).then((p) => { previews = p; if (list.isConnected) draw(); }).catch(() => {});
  }
  const banner = h("div", { class: "resume-bar" });
  const drawResume = () => {
    const closed = ws.saved.filter((r) => !r.open);
    recent.hidden = !closed.length;
    recent.textContent = `↺ Recent (${closed.length})`;
    const last = ws.saved.filter((r) => r.open);
    banner.hidden = !last.length;
    banner.replaceChildren(
      h("span", {}, `${last.length} ${last.length === 1 ? "terminal" : "terminals"} open last time: `,
        h("span", { class: "faint" }, last.map((r) => r.title).join(", "))),
      h("span", { class: "grow" }),
      h("button", { class: "btn primary", onclick: () => resume(last) }, "Resume all"),
      h("button", { class: "icon", title: "Not now (they stay under Recent)", onclick: () => ws.dismiss() }, "×"));
  };
  ws.onChange = drawResume;
  drawResume();
  drawFlags();

  const empty = h("div", { class: "empty faint" },
    "Open an agent or a plain terminal. Plain terminals are for your own git work: pull, branch, push, review.");
  const syncEmpty = () => (empty.hidden = ws.panes.length > 0);
  const observer = new MutationObserver(syncEmpty);
  observer.observe(ws.grid, { childList: true });
  syncEmpty();

  const el = h("div", { class: "workspace" },
    h("div", { class: "toolbar" },
      recent,
      what, flags, quick, h("span", { class: "faint small" }, "in"), where.el,
      h("button", { class: "btn primary", onclick: open }, "Open"),
      h("span", { class: "grow" }),
      layout),
    banner,
    empty,
    ws.grid);
  ws.fitAll();
  return { el, dispose: () => { observer.disconnect(); ws.onChange = () => {}; } };
}
