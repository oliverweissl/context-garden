import { api } from "../api.js";
import { h, toast, errText, setCrumbs, badge } from "../ui.js";
import { loginTerminal } from "../terminals.js";

const ENV = { claude: "CLAUDE_CONFIG_DIR", codex: "CODEX_HOME" };
const ICONS = ["●", "◆", "▲", "■", "★", "♥", "✿", "⚡"];
const MAX_ICON = 4; // characters as seen (an emoji with modifiers counts once)

/** First `n` user-perceived characters, so emoji sequences are never cut in half. */
const clip = (text, n) => [...new Intl.Segmenter().segment(text)].slice(0, n).map((x) => x.segment).join("");
const COLORS = ["#46d872", "#5bb0ff", "#a68cff", "#f0b43c", "#ff7a8f", "#ee8f4f", "#3fd6c6", "#f1f4ee"];

export async function renderAccounts(app) {
  setCrumbs(h("span", {}, "Accounts"));
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

  /** Accounts start without plugins: show whether the skills are there, offer to install. */
  function skillsLine(p) {
    if (!["claude", "codex"].includes(p.agent)) return null;
    const line = h("div", { class: "row between small" }, h("span", { class: "faint" }, "Checking skills…"));
    const install = async (btn) => {
      btn.disabled = true;
      btn.textContent = "Installing…";
      try {
        await api.installPlugin(p.id);
        toast(`Skills installed for ${p.name}; new terminals load them`);
      } catch (e) {
        toast(errText(e), "error");
      }
      show();
    };
    const show = async () => {
      const version = await api.pluginVersion(p.id).catch(() => null);
      const has = !!version;
      line.replaceChildren(has
        ? h("span", { class: "muted" }, `✓ context-garden ${version}`)
        : h("span", { class: "warn" }, "No context-garden skills in this account"),
      has ? null : h("button", { class: "btn", onclick: (e) => install(e.target) }, "Install skills"));
    };
    show();
    return line;
  }

  async function draw() {
    const profiles = await api.profiles();
    list.replaceChildren(...profiles.map((p) => h("div", { class: "card" },
      h("div", { class: "row between" },
        h("div", { class: "row" }, badge(p), h("h3", {}, p.name)),
        h("span", { class: "chip" }, agents.find((a) => a.id === p.agent)?.label || p.agent)),
      h("div", { class: "faint small mono ellipsis", title: p.dir }, `${ENV[p.agent]}=${p.dir}`),
      skillsLine(p),
      h("div", { class: "row end" },
        h("button", {
          class: "btn ghost",
          onclick: async () => { await api.removeProfile(p.id); draw(); },
        }, "Remove"),
        h("button", { class: "btn", onclick: () => login(p) }, "Sign in")))));
    if (!profiles.length) list.append(h("div", { class: "faint small" }, "No extra accounts yet. Terminals use your default login."));
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
    h("section", { class: "hero" }, h("p", { class: "kicker" }, "subscriptions"), h("h1", {}, "Accounts")),
    h("p", { class: "muted narrow" },
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
    list,
    signIn);
  await draw();
  return () => session?.close();
}
