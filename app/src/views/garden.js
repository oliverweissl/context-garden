import { api } from "../api.js";
import { h, toast, errText, setCrumbs, tile, fmt, languageBar, tokenDonut, modal, SKILL_COLORS } from "../ui.js";
import { cloneBoard, READY } from "./cloning.js";
import { planCard, SOURCES_HINT, LINK_HELP } from "./plant.js";
import { skillPicker, pluginPanel } from "./skills.js";
import { terminalArea } from "../terminals.js";
import { gardenArt } from "../art.js";

export async function renderGarden(app, id) {
  let garden;
  try {
    garden = await api.garden(id);
  } catch (e) {
    toast(errText(e), "error");
    location.hash = "#/";
    return;
  }
  setCrumbs(h("span", {}, garden.name));

  const tiles = h("div", { class: "tiles" }, h("div", { class: "faint small" }, "Reading garden…"));
  const langs = h("div");
  const boardSlot = h("div");
  let board = null;
  const repoTable = h("div", { class: "repo-table" }, h("div", { class: "faint small" }, "Reading garden…"));
  const extra = h("div");
  const missingSlot = h("div");

  // Repository table: click a header to sort by it, again to flip the order.
  let repos = [];
  const sort = { key: "lines", desc: true };
  const COLUMNS = [
    ["Repository", "name"], ["Branch", "branch"], ["Lines", "lines"],
    ["Changes", "changed"], ["Agent file tokens", "agent_tokens"], ["Facts", "seedbank_facts"],
  ];
  const TEXT = new Set(["name", "branch"]);
  const byText = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  function drawRepos() {
    const { key, desc } = sort;
    const value = (r) => (key === "branch" ? r.branch || r.status : r[key] ?? 0);
    const rows = [...repos].sort((a, b) => {
      const d = TEXT.has(key) ? byText.compare(value(a), value(b)) : value(a) - value(b);
      return (desc ? -d : d) || byText.compare(a.name, b.name);
    });
    const most = Math.max(1, ...repos.map((r) => r.lines));
    repoTable.replaceChildren(h("table", {},
      h("thead", {}, h("tr", {}, COLUMNS.map(([label, k]) => h("th", { "aria-sort": k === key ? (desc ? "descending" : "ascending") : "none" },
        h("button", {
          class: `sort${k === key ? " active" : ""}`,
          onclick: () => {
            if (sort.key === k) sort.desc = !sort.desc;
            else Object.assign(sort, { key: k, desc: !TEXT.has(k) }); // names A→Z, numbers biggest first
            drawRepos();
          },
        }, label, h("span", { class: "arrow" }, k === key ? (desc ? "▼" : "▲") : "")))))),
      h("tbody", {}, rows.map((r) => h("tr", { class: ["failed", "missing"].includes(r.status) ? "bad" : "" },
        h("td", {}, h("b", {}, r.name), h("div", { class: "faint small mono ellipsis" }, r.path)),
        h("td", { class: "mono small" }, r.branch || r.status),
        h("td", { class: "size", title: `${r.lines.toLocaleString()} lines · ${r.files.toLocaleString()} files` },
          h("span", { class: "size-bar", style: `width:${(100 * r.lines) / most}%` }), fmt(r.lines)),
        h("td", {}, r.changed || "–"),
        h("td", {}, r.agent_tokens ? fmt(r.agent_tokens) : "–"),
        h("td", {}, r.seedbank_facts || "–"))))));
  }

  const refreshBtn = h("button", { class: "icon refresh", title: "Refresh numbers", onclick: () => refresh() }, "↻");
  async function refresh() {
    refreshBtn.classList.add("spin");
    try {
      const s = await api.stats(id);
      const n = s.repos.length - s.missing;
      tiles.replaceChildren(
        tile("Repositories", n, s.missing ? `${s.missing} deleted from disk`
          : s.groups ? `${s.groups} groups · ${s.namespaces} folders` : "added one by one"),
        tile("Code", fmt(s.lines), `lines in ${fmt(s.files)} tracked files`),
        tile("Uncommitted", s.dirty, s.dirty === 1 ? "repo with changes" : "repos with changes", n ? s.dirty / n : 0),
        tile("Agent files", fmt(s.agent_tokens), "tokens always loaded (AGENTS.md + CLAUDE.md)"),
        tile("Seedbank", s.seedbank_facts, "cached facts"),
        tile("Greenhouse", s.greenhouse_cards ?? "–", s.greenhouse_cards == null ? "no base yet" : "repo cards",
          s.greenhouse_cards == null ? null : n ? s.greenhouse_cards / n : 0),
        tokenDonut(s.accounts, s.days));
      langs.replaceChildren(languageBar(s.languages));
      missingSlot.replaceChildren(s.missing ? h("div", { class: "card row between" },
        h("span", { class: "small" }, `${s.missing} ${s.missing === 1 ? "repository was" : "repositories were"} deleted from disk.`),
        h("button", {
          class: "btn",
          onclick: async () => { garden = await api.forgetMissing(id); refresh(); },
        }, "Remove from garden")) : "");
      repos = s.repos;
      drawRepos();
    } catch (e) {
      toast(errText(e), "error");
    }
    refreshBtn.classList.remove("spin");
  }

  const skillDots = () => Object.entries(garden.skills).filter(([, m]) => m === "on")
    .map(([s]) => h("span", { class: "chip", style: `--c:${SKILL_COLORS[s]}` }, s));
  const dots = h("div", { class: "chips" }, skillDots());

  /** Clone progress, shown while repos are being fetched or some failed. */
  async function showBoard() {
    board?.dispose();
    board = cloneBoard(garden, {
      onReady: (g) => {
        garden.repos = g.repos;
        if (g.repos.every((r) => READY.has(r.status))) {
          board?.dispose();
          board = null;
          boardSlot.replaceChildren();
        }
        refresh();
      },
    });
    boardSlot.replaceChildren(board.el);
    await board.watch();
  }

  const addPanel = (close) => {
    const sources = h("textarea", { rows: 4, class: "mono", placeholder: SOURCES_HINT });
    const preview = h("div");
    let plan = null;
    const go = h("button", { class: "btn primary" }, "Find repositories");
    const lookup = async () => {
      go.disabled = true;
      plan = null;
      try {
        plan = await api.plan({ garden: id, root: "", flat: false, protocol: "", inputs: sources.value.split("\n") });
      } catch (e) {
        toast(errText(e), "error");
      }
      go.disabled = false;
      preview.replaceChildren(plan ? planCard(plan, lookup) : "");
      if (plan && !plan.repos.length) plan = null;
      go.textContent = plan ? `Add ${plan.repos.length}` : "Find repositories";
    };
    go.onclick = async () => {
      if (!plan) return lookup();
      go.disabled = true;
      try {
        garden = await api.addToGarden(id, plan);
        close();
        await showBoard();
      } catch (e) {
        toast(errText(e), "error");
        go.disabled = false;
      }
    };
    sources.addEventListener("input", () => { plan = null; go.textContent = "Find repositories"; preview.replaceChildren(); });
    return h("div", {},
      h("div", { class: "form" },
        h("label", {}, h("span", {}, "Add repositories or groups",
          h("em", {}, `same layout as the garden (${garden.layout}, ${garden.protocol})`),
          h("span", { class: "info", title: LINK_HELP }, "i")), sources),
        h("div", { class: "row end" }, go)),
      preview);
  };

  const toggle = (panel, build) => async () => {
    if (extra.dataset.panel === panel) {
      extra.dataset.panel = "";
      return extra.replaceChildren();
    }
    extra.dataset.panel = panel;
    extra.replaceChildren(await build());
  };

  const openRepos = () => modal(`${garden.repos.length} repositories`, repoTable, { wide: true });
  const openAdd = () => {
    const m = modal("Add repositories or groups", h("div"), { wide: true });
    m.body.replaceChildren(addPanel(m.close));
  };
  const openSkills = async () => {
    const m = modal("Skills", h("div", { class: "faint small" }, "Loading…"), { wide: true });
    m.body.replaceChildren(await skillPicker(garden, (modes) => {
      garden.skills = modes;
      dots.replaceChildren(...skillDots());
      m.close();
      refresh();
    }), pluginPanel(garden));
  };

  const head = h("div", { class: "garden-head" },
    gardenArt(garden.id, garden.repos.length),
    h("div", {},
      h("h1", { class: "title" }, garden.name),
      h("div", { class: "faint small mono" }, garden.root)),
    dots,
    h("span", { class: "grow" }),
    h("button", { class: "btn", onclick: openRepos }, "Repos"),
    h("button", { class: "btn", onclick: openAdd }, "Add"),
    h("button", { class: "btn", onclick: openSkills }, "Skills"),
    h("button", {
      class: "btn ghost",
      onclick: toggle("forget", () => h("div", { class: "card row between" },
        h("span", { class: "small" }, "Remove this garden from the app? Repositories on disk are not touched."),
        h("button", {
          class: "btn danger",
          onclick: async () => { await api.forget(id); location.hash = "#/"; },
        }, "Remove"))),
    }, "…"));

  const terms = await terminalArea(garden);
  app.append(h("div", { class: "dash" }, head, tiles, missingSlot, h("div", { class: "dash-meta" }, langs, refreshBtn), extra, boardSlot), terms.el);
  refresh();
  if (!garden.repos.every((r) => READY.has(r.status))) showBoard();
  return () => { board?.dispose(); terms.dispose(); };
}
