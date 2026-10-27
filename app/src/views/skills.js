import { api } from "../api.js";
import { h, toast, errText, SKILL_COLORS, SKILL_BLURBS } from "../ui.js";

const MODES = ["on", "manual", "off"];

/** Skill chooser for a garden. Preselects the recommendation unless the garden already has modes. */
export async function skillPicker(garden, onApplied) {
  const rec = await api.recommend(garden.id);
  const modes = { ...(Object.keys(garden.skills).length ? garden.skills : rec.modes) };

  const rows = h("div", { class: "skill-rows" });
  const drawRows = () => rows.replaceChildren(...Object.keys(rec.modes).map((skill) =>
    h("div", { class: "skill-row" },
      h("span", { class: "dot", style: `background:${SKILL_COLORS[skill]}` }),
      h("div", { class: "skill-name" }, h("b", {}, skill), h("div", { class: "faint small" }, SKILL_BLURBS[skill])),
      h("div", { class: "seg" }, MODES.map((m) =>
        h("button", {
          class: modes[skill] === m ? "active" : "",
          onclick: () => { modes[skill] = m; presetSel.value = ""; drawRows(); },
        }, m))))));

  const presetSel = h("select", {
    onchange: () => {
      const p = rec.presets.find((x) => x.name === presetSel.value);
      if (!p) return;
      for (const s of Object.keys(modes)) modes[s] = p.on.includes(s) ? "on" : "manual";
      drawRows();
    },
  },
  h("option", { value: "" }, "custom"),
  rec.presets.map((p) => h("option", { value: p.name, selected: !Object.keys(garden.skills).length && p.name === rec.preset },
    `${p.name}${p.name === rec.preset ? " (recommended)" : ""} — ${p.description}`)));

  const apply = h("button", {
    class: "btn primary",
    onclick: async () => {
      apply.disabled = true;
      try {
        const errors = await api.applySkills(garden.id, modes);
        if (errors.length) toast(errors.join("\n"), "error");
        else toast("Skills written to every repo's .claude/settings.local.json");
        onApplied?.(modes);
      } catch (e) {
        toast(errText(e), "error");
      } finally {
        apply.disabled = false;
      }
    },
  }, "Apply skills");

  drawRows();
  return h("div", { class: "card skills" },
    h("div", { class: "row between" },
      h("h3", {}, "Context Garden skills"),
      h("label", { class: "inline" }, h("span", { class: "faint small" }, "Preset"), presetSel)),
    h("p", { class: "muted small" }, rec.reason,
      " on = agents use it automatically, manual = only when you call it. Needs the context-garden plugin installed in Claude Code."),
    rows,
    h("div", { class: "row end" }, apply));
}

/**
 * Other people's plugins (skills, agents, MCP servers) for this garden, from
 * any Claude Code plugin marketplace: a GitHub link, owner/repo or git URL.
 * Each Claude account gets them installed but off; the garden turns them on.
 */
export function pluginPanel(garden) {
  const list = h("div", { class: "skill-rows" });
  const offer = h("div");
  const input = h("input", { type: "text", placeholder: "https://github.com/owner/repo  or  owner/repo", spellcheck: false });
  const look = h("button", { class: "btn", onclick: () => lookUp() }, "Look up");
  input.addEventListener("keydown", (e) => e.key === "Enter" && lookUp());

  const drawList = () => list.replaceChildren(...(garden.plugins || []).map((p) =>
    h("div", { class: "skill-row" },
      h("div", { class: "skill-name" }, h("b", {}, p.id), h("div", { class: "faint small" }, p.description || p.source)),
      h("button", {
        class: "btn ghost", title: "Turn off in this garden; accounts keep it installed",
        onclick: async (e) => {
          e.target.disabled = true;
          try {
            const errors = await api.removePlugin(garden.id, p.id);
            garden.plugins = garden.plugins.filter((x) => x.id !== p.id);
            if (errors.length) toast(errors.join("\n"), "error");
            drawList();
          } catch (err) {
            toast(errText(err), "error");
            e.target.disabled = false;
          }
        },
      }, "Remove"))));

  async function lookUp() {
    if (!input.value.trim()) return;
    look.disabled = true;
    offer.replaceChildren(h("p", { class: "faint small" }, "Reading the marketplace…"));
    try {
      showOffer(await api.inspectPlugins(input.value));
    } catch (e) {
      offer.replaceChildren(h("p", { class: "small bad" }, errText(e)));
    } finally {
      look.disabled = false;
    }
  }

  function showOffer(info) {
    const have = new Set((garden.plugins || []).map((p) => p.id));
    const boxes = info.plugins.map((p) => {
      const box = h("input", { type: "checkbox", checked: info.plugins.length === 1 || !have.has(`${p.name}@${info.marketplace}`) });
      return { p, box, el: h("label", { class: "check plugin-pick" }, box,
        h("div", {},
          h("b", {}, `${p.name}@${info.marketplace}`), have.has(`${p.name}@${info.marketplace}`) ? h("span", { class: "faint small" }, " (added)") : null,
          h("div", { class: "faint small" }, p.description),
          p.missing.length ? h("div", { class: "small warn" },
            `Needs ${p.missing.map((c) => `\`${c}\``).join(", ")} on your PATH for its tools; see the project's README.`) : null)) };
    });
    const add = h("button", {
      class: "btn primary",
      onclick: async () => {
        const picked = boxes.filter((b) => b.box.checked).map((b) => ({ name: b.p.name, description: b.p.description }));
        if (!picked.length) return toast("Pick at least one plugin", "error");
        add.disabled = true;
        add.textContent = "Installing into your Claude accounts…";
        try {
          const problems = await api.addPlugins(garden.id, info, picked);
          garden.plugins = (await api.garden(garden.id)).plugins;
          drawList();
          offer.replaceChildren();
          input.value = "";
          if (problems.length) toast(problems.join("\n"), "error");
          else toast("Added; new terminals in this garden load it");
        } catch (e) {
          toast(errText(e), "error");
          add.disabled = false;
          add.textContent = "Add to garden";
        }
      },
    }, "Add to garden");
    offer.replaceChildren(h("div", { class: "plugin-offer" },
      h("p", { class: "muted small" }, `Marketplace “${info.marketplace}” from ${info.source}. Only add plugins you trust: they run with your agent's permissions.`),
      boxes.map((b) => b.el),
      h("div", { class: "row end" }, add)));
  }

  drawList();
  return h("div", { class: "card skills" },
    h("h3", {}, "External skills"),
    h("p", { class: "muted small" },
      "Claude Code plugins from GitHub or any git host (skills, agents, MCP servers). ",
      "Every Claude account gets them installed but off; only this garden's folders turn them on."),
    list,
    h("div", { class: "row" }, input, look),
    offer);
}
