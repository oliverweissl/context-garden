/*
 * Garden art: like an identicon, a garden's id is hashed into a small,
 * stable picture. 16 plots in a row, every plant drawn from the hash:
 *   type        4  (flower, leafy green, bush, vegetable)
 *   bloom color 64
 *   stem color  16
 *   stem width  16
 *   bloom size  16
 *   height      16
 * The number of plants follows the number of repos (at least 3, at most 16).
 * Wind: tall plants bend slowly from the base, small ones only stir their leaves.
 */
import { h } from "./ui.js";

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};

/** 32-bit string hash (cyrb53, low half) and a small PRNG seeded from it. */
function hash(str) {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  return h1 >>> 0;
}

function prng(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = (rand, n) => Math.floor(rand() * n);

// 64 bloom colors: 16 hues x 4 lightness/saturation steps.
const BLOOMS = Array.from({ length: 64 }, (_, i) => {
  const hue = (i % 16) * 22.5;
  const [sat, light] = [[85, 62], [75, 72], [90, 55], [60, 80]][i >> 4];
  return `hsl(${hue} ${sat}% ${light}%)`;
});
// 16 stem colors: greens through olive and brown.
const STEMS = Array.from({ length: 16 }, (_, i) =>
  `hsl(${70 + i * 6 - (i > 12 ? 60 : 0)} ${45 + (i % 4) * 10}% ${26 + (i % 5) * 6}%)`);
const TYPES = ["flower", "leafy", "bush", "vegetable"];

export function plantsFor(seedText, count) {
  const rand = prng(hash(seedText));
  const n = Math.max(3, Math.min(16, count || 0));
  // Spread n plants over the 16 plots, keeping the hash-chosen order stable.
  const plots = Array.from({ length: 16 }, (_, i) => i).sort(() => rand() - 0.5).slice(0, n).sort((a, b) => a - b);
  return plots.map((plot) => ({
    plot,
    type: TYPES[pick(rand, 4)],
    bloom: BLOOMS[pick(rand, 64)],
    stem: STEMS[pick(rand, 16)],
    width: 0.6 + pick(rand, 16) * 0.12,
    size: 1.6 + pick(rand, 16) * 0.22,
    height: 10 + pick(rand, 16) * 2.4,
    lean: (rand() - 0.5) * 4,
  }));
}

const TALL = 28; // plant height (of 10..46) from which the whole plant sways

/** Slow wind, phase-shifted per plot so the row never moves in step. */
const wind = (el, kind, ox, oy, p, i = 0) => {
  el.setAttribute("class", kind);
  el.style.transformOrigin = `${ox}px ${oy}px`;
  el.style.animationDuration = `${(kind === "sway" ? 9 : 5) + ((p.plot * 7 + i * 3) % 5) * 0.6}s`;
  el.style.animationDelay = `${-((p.plot * 1.7 + i * 0.9) % 9)}s`;
  return el;
};

function drawPlant(parent, p, x, ground) {
  const g = s("g");
  parent.append(g);
  const top = ground - p.height;
  const tall = p.height >= TALL;
  if (tall) wind(g, "sway", x, ground, p);
  let leaves = 0;
  // Small plants: each leaf flutters around where it joins the stem.
  const stir = (el, ox, oy) => (tall ? el : wind(el, "flutter", ox, oy, p, leaves++));
  const tipX = x + p.lean;
  const stem = (y1) => s("path", {
    d: `M${x} ${ground} Q${x} ${(ground + y1) / 2} ${tipX} ${y1}`,
    stroke: p.stem, "stroke-width": p.width, fill: "none", "stroke-linecap": "round",
  });
  const leaf = (y, dir, len) => stir(s("path", {
    d: `M${x + p.lean * 0.3} ${y} q${dir * len * 0.6} ${-len * 0.7} ${dir * len} ${-len * 0.2} q${-dir * len * 0.5} ${len * 0.5} ${-dir * len} ${len * 0.2}z`,
    fill: p.stem,
  }), x + p.lean * 0.3, y);

  if (p.type === "flower") {
    g.append(stem(top), leaf(ground - p.height * 0.35, -1, 3 + p.size), leaf(ground - p.height * 0.55, 1, 2.5 + p.size));
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2;
      g.append(s("circle", { cx: tipX + Math.cos(a) * p.size * 0.75, cy: top + Math.sin(a) * p.size * 0.75, r: p.size * 0.6, fill: p.bloom }));
    }
    g.append(s("circle", { cx: tipX, cy: top, r: p.size * 0.45, fill: "#f5d76e" }));
  } else if (p.type === "leafy") {
    // Lettuce / herb: a fan of leaves, flower color tints the leaf tips.
    const fan = 5 + Math.round(p.size);
    for (let i = 0; i < fan; i++) {
      const a = -Math.PI / 2 + ((i / (fan - 1)) - 0.5) * 2.2;
      const len = p.height * (0.45 + 0.25 * Math.cos(a + Math.PI / 2));
      const ex = x + Math.cos(a) * len, ey = ground + Math.sin(a) * len;
      g.append(stir(s("path", {
        d: `M${x} ${ground} Q${(x + ex) / 2 - 2} ${(ground + ey) / 2} ${ex} ${ey} Q${(x + ex) / 2 + 2} ${(ground + ey) / 2} ${x} ${ground}z`,
        fill: i % 2 ? p.stem : `color-mix(in srgb, ${p.stem} 70%, ${p.bloom})`,
      }), x, ground));
    }
  } else if (p.type === "bush") {
    const r = 2.5 + p.size * 0.9;
    const cy = ground - Math.min(p.height * 0.45, r * 1.6);
    g.append(stem(cy));
    // Foliage and berries stir together around the top of the stem.
    const crown = stir(s("g"), tipX, cy + r);
    for (const [dx, dy, k] of [[-r * 0.7, 0.3 * r, 0.85], [r * 0.7, 0.3 * r, 0.85], [0, -0.35 * r, 1]]) {
      crown.append(s("circle", { cx: tipX + dx, cy: cy + dy, r: r * k, fill: p.stem }));
    }
    for (let i = 0; i < 4; i++) {
      crown.append(s("circle", { cx: tipX + (i - 1.5) * r * 0.5, cy: cy + ((i % 2) - 0.4) * r * 0.5, r: 0.45 + p.size * 0.12, fill: p.bloom }));
    }
    g.append(crown);
  } else {
    // Vegetable: a leafy top above a root or fruit in the bloom color.
    g.append(stem(top), leaf(top + 2, -1, 2 + p.size), leaf(top + 3, 1, 2 + p.size));
    const r = 1.5 + p.size * 0.5;
    g.append(s("ellipse", { cx: tipX, cy: top + p.height * 0.45, rx: r, ry: r * 1.15, fill: p.bloom }));
  }
}

/** SVG banner for a garden card: 16 plots on a soil line. */
export function gardenArt(seedText, repoCount) {
  const W = 160, H = 64, ground = 56;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "garden-art", "aria-hidden": "true", preserveAspectRatio: "xMidYMax meet" });
  svg.append(s("rect", { x: 0, y: ground, width: W, height: H - ground, fill: "#1a130c" }));
  for (let i = 0; i < 16; i++) {
    svg.append(s("rect", { x: i * 10 + 2, y: ground + 2, width: 6, height: 1.2, rx: 0.6, fill: "#2b2015" }));
  }
  const g = s("g");
  for (const p of plantsFor(seedText, repoCount)) drawPlant(g, p, p.plot * 10 + 5, ground);
  svg.append(g);
  return h("div", { class: "art-wrap" }, svg);
}
