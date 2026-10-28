import { api } from "../api.js";
import { h, toast, errText, badge } from "../ui.js";
import { loginTerminal } from "../terminals.js";

const ENV = { claude: "CLAUDE_CONFIG_DIR", codex: "CODEX_HOME" };
const ICONS = ["●", "◆", "▲", "■", "★", "♥", "✿", "⚡"];
const MAX_ICON = 4; // characters as seen (an emoji with modifiers counts once)

/** First `n` user-perceived characters, so emoji sequences are never cut in half. */
const clip = (text, n) => [...new Intl.Segmenter().segment(text)].slice(0, n).map((x) => x.segment).join("");
const COLORS = ["#46d872", "#5bb0ff", "#a68cff", "#f0b43c", "#ff7a8f", "#ee8f4f", "#3fd6c6", "#f1f4ee"];

const CG = "context-garden@context-garden";

/** Accounts settings: the app's accounts, the agents' default logins, their plugins. Returns a cleanup. */
export async function accountsPanel(app) {
  const agents = (await api.agents()).filter((a) => a.profiles);
  const list = h("div", { class: "grid" });
  const signIn = h("div");
  let session = null;

  /** Sign-in terminal for an account; the CLI opens the browser login. */
  async function login(p) {
    session?.close();
    const a = agents.find((x) => x.id === p.agent);
    if (a && !a.found) return toast(`${a.label} is not installed`, "error");
    const hint = p.agent === "codex"
      ? "Your browser opens to sign in; this closes when done."
      : "Pick a theme, then sign in from the browser window that opens. Already set up? Type /login.";
    const slot = h("div");
    signIn.replaceChildren(h("div", { class: "card login" },
      h("div", { class: "row between" },
        h("div", { class: "row" }, badge(p), h("h3", {}, `Sign in: ${p.name}`)),
        h("button", { class: "btn", onclick: () => { session?.close(); session = null; signIn.replaceChildren(); } }, "Done")),
      h("div", { class: "faint small" }, hint),
      slot));
    try {
      session = await loginTerminal(p, slot);
    } catch (e) {
      signIn.replaceChildren();
      return toast(errText(e), "error");
    }
    signIn.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /**
   * Plugins of an account (or a default login): each with its version and an
   * update button; context-garden is offered for install when missing.
   */
  function pluginsBlock(p) {
    if (!["claude", "codex"].includes(p.agent)) return null;
    const block = h("div", { class: "plugin-list small" }, h("span", { class: "faint" }, "Reading plugins…"));
    const profile = p.default ? null : p.id;
    const busy = async (btn, label, work, done) => {
      btn.disabled = true;
      btn.textContent = label;
      try {
        await work();
        toast(done);
      } catch (e) {
        toast(errText(e), "error");
      }
      show();
    };
    const show = async () => {
      let list = [];
      try {
        list = await api.accountPlugins(p.agent, profile);
      } catch (e) {
        return block.replaceChildren(h("span", { class: "bad" }, errText(e)));
      }
      // Offline or GitHub unreachable: just no update hint.
      const cgBehind = profile && list.some((x) => x.id === CG) ? await api.pluginOutdated(profile).catch(() => false) : false;
      const rows = list.map((x) => {
        const newer = x.latest || (x.id === CG && cgBehind);
        const name = x.id.endsWith(`@${x.id.split("@")[0]}`) ? x.id.split("@")[0] : x.id;
        return h("div", { class: "plugin-line" },
          h("span", { class: "ellipsis", title: x.id }, name),
          h("span", { class: newer ? "warn" : "faint" }, x.version,
            newer ? ` → ${x.latest || "newer on main"}` : ""),
          h("span", { class: "grow" }),
          h("button", {
            class: `btn small-btn${newer ? "" : " ghost"}`,
            title: newer ? "Install the newer version" : "Refresh its marketplace and install the newest version",
            onclick: (e) => busy(e.target, "Updating…", () => api.updateAccountPlugin(p.agent, profile, x.id),
              `${name} updated for ${p.name}; restart its terminals to load it`),
          }, newer ? "Update" : "Check"));
      });
      if (!list.some((x) => x.id === CG) && profile) {
        rows.unshift(h("div", { class: "plugin-line" },
          h("span", { class: "warn" }, "No context-garden skills in this account"),
          h("span", { class: "grow" }),
          h("button", {
            class: "btn small-btn",
            onclick: (e) => busy(e.target, "Installing…", () => api.installPlugin(p.id),
              `Skills installed for ${p.name}; new terminals load them`),
          }, "Install")));
      }
      block.replaceChildren(...(rows.length ? rows : [h("span", { class: "faint" }, "No plugins")]));
    };
    show();
    return block;
  }

  async function draw() {
    const profiles = await api.profiles();
    list.replaceChildren(...profiles.map((p) => h("div", { class: "card" },
      h("div", { class: "row between" },
        h("div", { class: "row" }, badge(p), h("h3", {}, p.name)),
        h("span", { class: "chip" }, agents.find((a) => a.id === p.agent)?.label || p.agent)),
      h("div", { class: "faint small mono ellipsis", title: p.dir }, `${ENV[p.agent]}=${p.dir}`),
      pluginsBlock(p),
      h("div", { class: "row end" },
        h("button", {
          class: "btn ghost",
          onclick: async () => { await api.removeProfile(p.id); draw(); },
        }, "Remove"),
        h("button", { class: "btn", onclick: () => login(p) }, "Sign in")))));
    // The logins the agents use without an app account.
    for (const a of agents.filter((x) => x.found)) {
      const p = { default: true, id: `default:${a.id}`, name: `${a.label} (default login)`, agent: a.id, icon: "", color: "" };
      list.append(h("div", { class: "card" },
        h("div", { class: "row between" }, h("h3", {}, p.name), h("span", { class: "chip" }, a.label)),
        h("div", { class: "faint small" }, "Terminals without an account use this login."),
        pluginsBlock(p)));
    }
  }

  const choice = { icon: ICONS[0], color: COLORS[0] };
  const name = h("input", { placeholder: "e.g. work, client-x, personal" });
  const agent = h("select", {}, agents.map((a) => h("option", { value: a.id }, a.label)));
  const dir = h("input", { class: "mono", placeholder: "optional: existing config dir, e.g. ~/.claude-work" });
  const custom = h("input", { class: "icon-input", placeholder: "name or emoji", title: `Optional: a short name or emoji, up to ${MAX_ICON} characters` });
  let picked = ICONS[0];

  const preview = h("span");
  const iconRow = h("div", { class: "swatches" });
  const colorRow = h("div", { class: "swatches" });
  const drawPicker = () => {
    preview.replaceChildren(badge(choice, true));
    iconRow.replaceChildren(...ICONS.map((i) => h("button", {
      class: `swatch ${choice.icon === i ? "active" : ""}`,
      onclick: () => { choice.icon = picked = i; custom.value = ""; drawPicker(); },
    }, i)), custom);
    colorRow.replaceChildren(...COLORS.map((c) => h("button", {
      class: `swatch color ${choice.color === c ? "active" : ""}`, style: `--c:${c}`, title: c,
      onclick: () => { choice.color = c; drawPicker(); },
    })));
  };
  custom.addEventListener("input", () => {
    const typed = clip(custom.value.trim(), MAX_ICON);
    if (typed !== custom.value.trim()) custom.value = typed;
    choice.icon = typed || picked;
    iconRow.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("active", !typed && b.textContent === picked));
    preview.replaceChildren(badge(choice, true));
  });
  drawPicker();

  const add = h("button", {
    class: "btn primary",
    onclick: async () => {
      try {
        const existing = dir.value.trim();
        const p = await api.addProfile(name.value, agent.value, dir.value, choice.icon, choice.color);
        name.value = dir.value = custom.value = "";
        choice.icon = picked;
        drawPicker();
        await draw();
        // A fresh config folder has no login and no plugins yet: sign in, and install the skills meanwhile.
        if (!existing) login(p);
        if (!(await api.pluginVersion(p.id).catch(() => true))) {
          api.installPlugin(p.id)
            .then(() => { toast(`Skills installed for ${p.name}`); draw(); })
            .catch((e) => toast(`Skills not installed: ${errText(e)}`, "error"));
        }
      } catch (e) {
        toast(errText(e), "error");
      }
    },
  }, "Add account");

  app.append(
    h("p", { class: "muted small" },
      "Each account is its own config folder (Claude Code: CLAUDE_CONFIG_DIR, Codex: CODEX_HOME). ",
      "The agent CLI handles login and keeps its credentials there or in the OS keychain; ",
      "this app never reads, copies or sends them. Removing an account here leaves its folder on disk."),
    h("div", { class: "card form" },
      h("div", { class: "row" },
        h("label", { class: "grow" }, h("span", {}, "Name"), name),
        h("label", {}, h("span", {}, "Agent"), agent)),
      h("label", {}, h("span", {}, "Folder"), dir),
      h("div", { class: "row picker" },
        // Not a <label>: it would forward clicks on re-rendered swatches to its first button.
        h("div", { class: "field grow" }, h("span", {}, "Icon"), iconRow, colorRow),
        preview),
      h("div", { class: "row end" }, add)),
    signIn,
    list);
  await draw();
  return () => session?.close();
}
