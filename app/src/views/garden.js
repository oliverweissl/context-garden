import { api } from "../api.js";
import { h, toast, errText, setCrumbs, tile, fmt, languageBar, tokenDonut, modal, SKILL_COLORS } from "../ui.js";
import { cloneBoard, READY, ACTIVE } from "./cloning.js";
import { planCard, SOURCES_HINT, LINK_HELP } from "./plant.js";
import { skillPicker, pluginPanel } from "./skills.js";
import { terminalArea } from "../terminals.js";
import { gardenArt } from "../art.js";
import { setPageSettings } from "./settings.js";

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

  /** Clone progress while repos are fetched; afterwards failures fold into one line. */
  async function showBoard() {
    board?.dispose();
    board = cloneBoard(garden, {
      onReady: (g) => {
        garden.repos = g.repos;
        if (g.repos.every((r) => READY.has(r.status))) hideBoard();
        refresh();
      },
      onHide: () => hideBoard(),
    });
    boardSlot.replaceChildren(board.el);
    await board.watch();
  }

  function hideBoard() {
    board?.dispose();
    board = null;
    const failed = garden.repos.filter((r) => r.status === "failed").length;
    boardSlot.replaceChildren(failed ? h("div", { class: "card row between" },
      h("span", { class: "small bad" }, `${failed} ${failed === 1 ? "repository" : "repositories"} could not be fetched.`),
      h("button", { class: "btn", onclick: () => showBoard() }, "Show and retry")) : "");
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
          h("em", {}, `same layout as the garden (${garden.layout}, ${garden.protocol === "auto" ? "protocol as typed" : garden.protocol})`),
          h("span", { class: "info", title: LINK_HELP }, "i")), sources),
        h("div", { class: "row end" }, go)),
      preview);
  };

  /** List the garden's groups again: offer new repos to clone and repos that left to forget. */
  const resyncPanel = async (slot, close) => {
    slot.replaceChildren(h("div", { class: "card faint small" }, "Listing groups again…"));
    let res;
    try {
      res = await api.resyncGroups(id);
    } catch (e) {
      toast(errText(e), "error");
      slot.replaceChildren();
      return;
    }
    const again = () => resyncPanel(slot, close);
    const parts = [];
    if (res.repos.length) {
      const add = h("button", { class: "btn primary" }, `Add ${res.repos.length}`);
      add.onclick = async () => {
        add.disabled = true;
        try {
          garden = await api.addToGarden(id, res);
          close();
          await showBoard();
        } catch (e) {
          toast(errText(e), "error");
          add.disabled = false;
        }
      };
      parts.push(planCard(res, again), h("div", { class: "row end" }, add));
    } else {
      parts.push(h("div", { class: "card" },
        h("div", { class: "small" }, `No new repositories in ${res.groups.length} ${res.groups.length === 1 ? "group" : "groups"}.`),
        res.warnings.length
          ? h("div", { class: "row between" },
            h("div", {}, res.warnings.map((w) => h("div", { class: "small warn" }, w))),
            h("button", { class: "btn", onclick: again }, "Look up again"))
          : null));
    }
    if (res.gone.length) {
      const goneCard = h("div", { class: "card" });
      const forget = h("button", { class: "btn" }, "Remove from garden");
      forget.onclick = async () => {
        forget.disabled = true;
        try {
          garden = await api.forgetRepos(id, res.gone.map((r) => r.path));
          goneCard.replaceChildren(h("div", { class: "small" }, `Removed ${res.gone.length} from the garden; their folders are still on disk.`));
          refresh();
        } catch (e) {
          toast(errText(e), "error");
          forget.disabled = false;
        }
      };
      goneCard.append(
        h("div", { class: "row between" },
          h("span", { class: "small" }, `${res.gone.length} ${res.gone.length === 1 ? "repository is" : "repositories are"} no longer in ${res.gone.length === 1 ? "its group" : "their groups"}. Folders on disk are not touched.`),
          forget),
        h("ul", { class: "repo-list" }, res.gone.map((r) => h("li", {},
          h("span", { class: "mono ellipsis" }, r.path), h("span", { class: "small faint" }, r.group)))));
      parts.push(goneCard);
    }
    slot.replaceChildren(...parts);
  };

  /** The repository table, with adding more and resyncing groups in the same popup. */
  const openRepos = () => {
    const addSlot = h("div");
    const addBtn = h("button", { class: "btn primary" }, "+ Add repositories or groups");
    const resyncBtn = garden.group_inputs.length
      ? h("button", { class: "btn", title: "List the groups this garden was built from again" }, "Resync groups")
      : null;
    const m = modal(`${garden.repos.length} repositories`, h("div", { class: "repos-pop" },
      h("div", { class: "row end" }, resyncBtn, addBtn), addSlot, repoTable), { wide: true });
    addBtn.onclick = () => {
      addBtn.hidden = true;
      if (resyncBtn) resyncBtn.hidden = false;
      addSlot.replaceChildren(h("div", { class: "card add-inline" }, addPanel(m.close)));
      addSlot.querySelector("textarea")?.focus();
    };
    if (resyncBtn) resyncBtn.onclick = () => {
      resyncBtn.hidden = true;
      addBtn.hidden = false;
      resyncPanel(addSlot, m.close);
    };
  };

  // This garden's part of Settings (the gear in the top bar), in front of Accounts.
  setPageSettings([
    { name: "Skills", build: async (close) => skillPicker(garden, (modes) => {
      garden.skills = modes;
      dots.replaceChildren(...skillDots());
      close();
      refresh();
    }) },
    { name: "External plugins", build: () => pluginPanel(garden) },
    { name: "Garden", build: () => h("div", { class: "card skills" },
      h("h3", {}, garden.name),
      h("div", { class: "faint small mono" }, garden.root),
      h("div", { class: "row between" },
        h("span", { class: "small" }, "Remove this garden from the app? Repositories on disk are not touched."),
        h("button", {
          class: "btn danger",
          onclick: async () => { await api.forget(id); location.hash = "#/"; },
        }, "Remove garden"))) },
  ]);

  const head = h("div", { class: "garden-head" },
    gardenArt(garden.id, garden.repos.length),
    h("div", {},
      h("h1", { class: "title" }, garden.name),
      h("div", { class: "faint small mono" }, garden.root)),
    dots,
    h("span", { class: "grow" }),
    h("button", { class: "btn", onclick: openRepos }, "Repositories"));

  const terms = await terminalArea(garden);
  app.append(h("div", { class: "dash" }, head, tiles, missingSlot, h("div", { class: "dash-meta" }, langs, refreshBtn), boardSlot), terms.el);
  refresh();
  if (garden.repos.some((r) => ACTIVE.has(r.status))) showBoard();
  else hideBoard();
  return () => { board?.dispose(); terms.dispose(); };
}
