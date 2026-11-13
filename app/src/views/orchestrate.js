import { api } from "../api.js";
import { h, modal, toast, errText, badge } from "../ui.js";

// Node positions are a per-viewer convenience; blocked storage just means default places.
const loadPref = (key, fallback) => { try { return JSON.parse(localStorage.getItem(`cg.${key}`)) ?? fallback; } catch { return fallback; } };
const savePref = (key, value) => { try { localStorage.setItem(`cg.${key}`, JSON.stringify(value)); } catch { /* private mode */ } };

const W = 200; // node size, so lines can be drawn without measuring the DOM
const H = 66;
const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

/**
 * Who manages whom among a garden's open agent terminals, as a graph: drag from a
 * node's handle onto another node to make the first manage the second, click the
 * x on a line to undo it. A manager can then prompt and read its workers, and
 * workers report back (the garden-control MCP tools; the app enforces these links).
 */
export async function openOrchestrate(ws, garden) {
  const panes = () => ws.panes.filter((p) => p.opts.agent || p.opts.profile);
  let links = [];
  try {
    links = await api.links(garden.id);
  } catch (e) {
    return toast(errText(e), "error");
  }
  const prefKey = `graph.${garden.id}`;
  const pos = loadPref(prefKey, {});
  const at = (p, i) => pos[p.opts.key] || { x: 24 + (i % 3) * (W + 36), y: 24 + Math.floor(i / 3) * (H + 44) };

  const canvas = h("div", { class: "og-canvas" });
  const lines = svg("svg", { class: "og-lines" });
  canvas.append(lines);
  const help = h("p", { class: "muted small" },
    "Drag from the ● on a terminal's right edge onto another terminal: the first becomes its manager and can prompt it, ",
    "read its screen and get reports back. Click ✕ on a line to remove it. Only agent terminals appear here.");

  const nodes = new Map(); // key -> element

  async function save(next) {
    const before = links;
    links = next;
    try {
      links = await api.setLinks(garden.id, next);
    } catch (e) {
      links = before;
      toast(errText(e), "error");
    }
    draw();
  }

  function draw() {
    const list = panes();
    const byKey = new Map(list.map((p, i) => [p.opts.key, { p, ...at(p, i) }]));
    for (const [key, el] of nodes) if (!byKey.has(key)) { el.remove(); nodes.delete(key); }
    list.forEach((p, i) => {
      const k = p.opts.key;
      const { x, y } = at(p, i);
      let el = nodes.get(k);
      if (!el) {
        el = h("div", { class: "og-node" });
        nodes.set(k, el);
        canvas.append(el);
        wire(el, p);
      }
      const manages = links.filter((l) => l.manager === k && byKey.has(l.worker)).length;
      const managed = links.some((l) => l.worker === k && byKey.has(l.manager));
      el.style.cssText = `left:${x}px;top:${y}px;width:${W}px;height:${H}px`;
      el.classList.toggle("master", manages > 0 && !managed);
      el.replaceChildren(
        h("div", { class: "og-title" }, p.opts.account ? badge(p.opts.account) : null, h("span", { class: "ellipsis" }, p.opts.title)),
        h("div", { class: "faint small ellipsis" }, p.opts.cwdLabel || "garden root"),
        h("div", { class: "og-role small" },
          manages ? h("span", { class: "chip" }, manages > 1 || !managed ? `★ manages ${manages}` : `manages ${manages}`) : null,
          managed ? h("span", { class: "chip" }, "worker") : null),
        h("span", { class: "og-handle", title: "Drag onto another terminal to make this one its manager" }));
    });
    lines.replaceChildren(svg("defs", {}));
    lines.firstChild.innerHTML = '<marker id="og-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker>';
    for (const l of links) {
      const a = byKey.get(l.manager);
      const b = byKey.get(l.worker);
      if (!a || !b) continue; // one of them is closed: the link stays, hidden
      const [x1, y1, x2, y2] = [a.x + W, a.y + H / 2, b.x, b.y + H / 2];
      const dx = Math.max(40, Math.abs(x2 - x1) / 2);
      lines.append(svg("path", { d: `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`, class: "og-edge", "marker-end": "url(#og-arrow)" }));
      const g = svg("g", { class: "og-cut", transform: `translate(${(x1 + x2) / 2} ${(y1 + y2) / 2})` });
      g.append(svg("circle", { r: 9 }), Object.assign(svg("text", { "text-anchor": "middle", dy: "4" }), { textContent: "✕" }));
      const title = svg("title");
      title.textContent = `Stop ${a.p.opts.title} managing ${b.p.opts.title}`;
      g.append(title);
      g.addEventListener("click", () => save(links.filter((x) => x !== l)));
      lines.append(g);
    }
    empty.hidden = list.length > 0;
    empty.textContent = list.length ? "" : "Open agent terminals (not plain shells) in this garden to link them.";
  }

  /** Move a node by its body, or start a link from its handle. */
  function wire(el, p) {
    const key = p.opts.key;
    el.addEventListener("pointerdown", (down) => {
      if (down.button !== 0) return;
      down.preventDefault();
      const box = canvas.getBoundingClientRect();
      if (down.target.classList.contains("og-handle")) {
        const temp = svg("path", { class: "og-edge temp" });
        lines.append(temp);
        const from = at(p, panes().indexOf(p));
        const move = (e) => {
          const x2 = e.clientX - box.left;
          const y2 = e.clientY - box.top;
          const x1 = from.x + W;
          const y1 = from.y + H / 2;
          temp.setAttribute("d", `M${x1} ${y1} L${x2} ${y2}`);
        };
        const up = (e) => {
          window.removeEventListener("pointermove", move);
          window.removeEventListener("pointerup", up);
          temp.remove();
          const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".og-node");
          const worker = [...nodes].find(([, n]) => n === target)?.[0];
          if (worker && worker !== key && !links.some((l) => l.manager === key && l.worker === worker)) {
            save([...links, { manager: key, worker }]);
          }
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        return;
      }
      const start = at(p, panes().indexOf(p));
      const ox = down.clientX - start.x;
      const oy = down.clientY - start.y;
      el.classList.add("drag");
      const move = (e) => {
        pos[key] = {
          x: Math.max(0, Math.min(box.width - W, e.clientX - ox)),
          y: Math.max(0, Math.min(box.height - H, e.clientY - oy)),
        };
        draw();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        el.classList.remove("drag");
        savePref(prefKey, pos);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  const empty = h("div", { class: "og-empty faint", hidden: true });
  canvas.append(empty);
  const m = modal("Orchestrate agents", h("div", {}, help, canvas), { wide: true });
  draw();
  return m;
}
