import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { api } from "./api.js";
import { h, toast, errText, badge } from "./ui.js";

const THEME = {
  background: "#040604", foreground: "#d9f2dc", cursor: "#46d872", cursorAccent: "#040604",
  selectionBackground: "rgba(70, 216, 114, 0.28)",
  black: "#0a0e0a", red: "#ff7a8f", green: "#46d872", yellow: "#f0b43c",
  blue: "#5bb0ff", magenta: "#a68cff", cyan: "#3fd6c6", white: "#f1f4ee",
  brightBlack: "#636c60", brightRed: "#ff9aab", brightGreen: "#7ce69b", brightYellow: "#f5cb6e",
  brightBlue: "#86c5ff", brightMagenta: "#c2b0ff", brightCyan: "#72e6da", brightWhite: "#ffffff",
};

const now = () => Math.floor(Date.now() / 1000);

/**
 * Terminal workspace of one garden. Lives across navigation (terminals keep
 * running while you look at another garden). What runs in each pane is saved
 * with the garden, so after a restart (or closing one) it can be resumed;
 * Claude panes get their own session id and resume exactly that conversation.
 */
class Workspace {
  constructor(gardenId, saved = []) {
    this.gardenId = gardenId;
    /** Saved records of panes that are not live: open at last quit, or closed. */
    this.saved = saved;
    this.onChange = () => {};
    this.layout = "grid";
    this.panes = [];
    this.grid = h("div", { class: "panes grid" });
    this.resizeObserver = new ResizeObserver(() => this.fitAll());
    this.resizeObserver.observe(this.grid);
  }

  setLayout(layout) {
    this.layout = layout;
    this.grid.className = `panes ${layout}`;
    this.fitAll();
  }

  fitAll() {
    requestAnimationFrame(() => this.panes.forEach((p) => p.fit()));
  }

  /** Open a pane. `opts.resume` reopens a saved one (same key and session). */
  async open(opts) {
    opts.key ||= crypto.randomUUID();
    const claude = opts.agent === "claude" || opts.account?.agent === "claude";
    if (claude && !opts.session) opts.session = crypto.randomUUID();
    this.saved = this.saved.filter((r) => r.key !== opts.key);
    const pane = new Pane(this, opts);
    this.panes.push(pane);
    this.grid.append(pane.el);
    this.persist();
    await pane.start();
    this.fitAll();
  }

  remove(pane) {
    this.panes = this.panes.filter((p) => p !== pane);
    pane.el.remove();
    this.saved.unshift({ ...record(pane.opts), open: false, used_at: now() });
    this.persist();
    this.fitAll();
  }

  /** Forget that saved panes were open (they stay under "Resume"). */
  dismiss() {
    this.saved = this.saved.map((r) => ({ ...r, open: false }));
    this.persist();
  }

  persist() {
    this.onChange();
    if (!this.gardenId) return;
    const live = this.panes.map((p) => ({ ...record(p.opts), open: true, used_at: now() }));
    api.saveTerminals(this.gardenId, [...live, ...this.saved]).catch(() => {});
  }

  /** Put `pane` at position `index` (0 = first). */
  moveTo(pane, index) {
    const from = this.panes.indexOf(pane);
    if (from < 0) return;
    this.panes.splice(from, 1);
    this.panes.splice(Math.max(0, Math.min(index, this.panes.length)), 0, pane);
    this.grid.replaceChildren(...this.panes.map((p) => p.el));
    this.persist();
    this.fitAll();
  }

  /**
   * Rearrange by dragging a pane's header: the pane follows the pointer and
   * drops before or after the pane under it. Pointer events, not HTML5 drag
   * and drop, which the desktop window reserves for dropping files.
   */
  drag(pane, down) {
    if (down.button !== 0 || down.target.closest("button") || this.panes.length < 2) return;
    const start = { x: down.clientX, y: down.clientY };
    let lifted = false;
    let target = null;
    const clear = () => this.panes.forEach((p) => p.el.classList.remove("drop-before", "drop-after"));
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
      // Side-by-side panes split left/right, stacked ones top/bottom.
      const horizontal = this.layout === "columns" || (this.layout === "grid" && r.width > r.height);
      const after = horizontal ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
      over.el.classList.add(after ? "drop-after" : "drop-before");
      target = { over, after };
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
      const rest = this.panes.filter((p) => p !== pane);
      this.moveTo(pane, rest.indexOf(target.over) + (target.after ? 1 : 0));
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

class Pane {
  constructor(ws, opts) {
    this.ws = ws;
    this.opts = opts;
    this.id = null;
    this.term = new Terminal({
      theme: THEME, fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      fontSize: 13, cursorBlink: true, allowProposedApi: false, scrollback: 5000,
    });
    this.fitter = new FitAddon();
    this.term.loadAddon(this.fitter);
    const body = h("div", { class: "pane-body" });
    this.status = h("span", { class: "pane-status live" });
    this.el = h("section", { class: "pane" },
      h("header", {
        class: "pane-head", title: "Drag to rearrange",
        onpointerdown: (e) => ws.drag(this, e),
      },
      this.status,
      opts.account ? badge(opts.account) : null,
      h("b", {}, opts.title),
      h("span", { class: "faint small ellipsis" }, opts.cwdLabel),
      h("span", { class: "grow" }),
      h("button", { class: "icon", title: "Maximize", onclick: () => this.toggleMax() }, "⤢"),
      h("button", { class: "icon", title: "Close", onclick: () => this.close() }, "×")),
      body);
    this.term.open(body);
    this.term.onData((d) => this.id != null && api.write(this.id, d).catch(() => {}));
    this.term.onResize(({ cols, rows }) => this.id != null && api.resize(this.id, cols, rows).catch(() => {}));
  }

  async start() {
    this.fitter.fit();
    try {
      const size = { cols: this.term.cols, rows: this.term.rows };
      const out = (bytes) => this.term.write(bytes);
      const exit = (code) => this.exited(code);
      this.id = this.opts.login
        ? await api.login({ profile: this.opts.login, ...size }, out, exit)
        : await api.spawn(
          { garden: this.ws.gardenId, cwd: this.opts.cwd, agent: this.opts.agent, profile: this.opts.profile,
            session: this.opts.session, resume: !!this.opts.resume, flags: this.opts.flags, ...size },
          out, exit);
      this.term.focus();
    } catch (e) {
      this.term.write(`\x1b[31m${errText(e)}\x1b[0m\r\n`);
      this.exited(null);
    }
  }

  exited(code) {
    this.id = null;
    this.status.className = "pane-status";
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
  ws.setLayout("rows");
  container.replaceChildren(ws.grid); // attached first so the terminal can measure itself
  await ws.open({ login: profile.id, account: profile, title: `Sign in · ${profile.name}`, cwdLabel: "" });
  return { close: () => [...ws.panes].forEach((p) => p.close()) };
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

  const where = h("select", {},
    h("option", { value: garden.root }, "garden root"),
    garden.repos.filter((r) => ["cloned", "linked", "local"].includes(r.status))
      .map((r) => h("option", { value: r.path }, r.name)));
  const layout = h("div", { class: "seg" }, ["grid", "columns", "rows"].map((l) =>
    h("button", {
      class: ws.layout === l ? "active" : "",
      onclick: (e) => {
        ws.setLayout(l);
        layout.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === e.target));
      },
    }, l)));

  const open = async () => {
    const [kind, id] = what.value.split(":");
    if (kind === "profile" && !(await api.pluginVersion(id).catch(() => true))) {
      toast("This account has no context-garden skills yet: Accounts → Install skills", "error");
    }
    const label = what.selectedOptions[0].textContent.replace(" (not installed)", "");
    try {
      await ws.open({
        cwd: where.value, cwdLabel: where.selectedOptions[0].textContent,
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
  };
  const ago = (t) => {
    const m = Math.round((now() - t) / 60);
    return m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
  };
  const recent = h("select", {
    title: "Reopen a terminal you closed; agents continue their conversation",
    onchange: () => {
      const r = ws.saved.find((x) => x.key === recent.value);
      recent.value = "";
      if (r) resume([r]);
    },
  });
  const banner = h("div", { class: "resume-bar" });
  const drawResume = () => {
    const closed = ws.saved.filter((r) => !r.open);
    recent.hidden = !closed.length;
    recent.replaceChildren(h("option", { value: "" }, `Resume… (${closed.length})`),
      closed.map((r) => h("option", { value: r.key }, `${r.title} · ${r.cwd_label || "garden root"} · ${ago(r.used_at)}`)));
    const last = ws.saved.filter((r) => r.open);
    banner.hidden = !last.length;
    banner.replaceChildren(
      h("span", {}, `${last.length} ${last.length === 1 ? "terminal" : "terminals"} open last time: `,
        h("span", { class: "faint" }, last.map((r) => r.title).join(", "))),
      h("span", { class: "grow" }),
      h("button", { class: "btn primary", onclick: () => resume(last) }, "Resume all"),
      h("button", { class: "icon", title: "Not now (they stay under Resume…)", onclick: () => ws.dismiss() }, "×"));
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
      what, flags, quick, h("span", { class: "faint small" }, "in"), where,
      h("button", { class: "btn primary", onclick: open }, "Open"),
      recent,
      h("span", { class: "grow" }),
      layout),
    banner,
    empty,
    ws.grid);
  ws.fitAll();
  return { el, dispose: () => { observer.disconnect(); ws.onChange = () => {}; } };
}
