import "@fontsource/manrope/400.css";
import "@fontsource/manrope/600.css";
import "@fontsource/saira-condensed/600.css";
import "@fontsource/saira-condensed/700.css";
import "@xterm/xterm/css/xterm.css";
import "./style.css";

import { renderHome } from "./views/home.js";
import { renderPlant } from "./views/plant.js";
import { renderGarden } from "./views/garden.js";
import { openSettings, setPageSettings } from "./views/settings.js";
import { setCrumbs, h, toast, errText } from "./ui.js";
import { api } from "./api.js";
import { initControl } from "./terminals.js";

const app = document.getElementById("app");
initControl();
let cleanup = null;

async function route() {
  if (cleanup) cleanup();
  cleanup = null;
  // Popups belong to the page they were opened on.
  document.querySelectorAll("dialog.modal").forEach((d) => d.close());
  setCrumbs();
  setPageSettings();
  const [, page, id] = location.hash.replace(/^#/, "").split("/");
  app.className = page === "garden" ? "full" : "";
  app.replaceChildren();
  if (page === "new") cleanup = await renderPlant(app);
  else if (page === "garden" && id) cleanup = await renderGarden(app, decodeURIComponent(id));
  else cleanup = await renderHome(app);
  if (page === "accounts") openSettings("Accounts"); // old links
}

window.addEventListener("hashchange", route);
route();

document.getElementById("settings").addEventListener("click", () => openSettings());

// Each window is independent and starts on the garden list.
const newWindow = () => api.newWindow().catch((e) => toast(errText(e), "error"));
document.getElementById("new-window").addEventListener("click", newWindow);
window.addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() === "n" && e.shiftKey && (e.ctrlKey || e.metaKey) && !e.altKey) {
    e.preventDefault();
    newWindow();
  }
});

// A newer release on GitHub shows up next to Settings; offline means no hint. Builds with an
// updater key install it in place (then restart); others, and installs that fail (a .deb or
// .rpm cannot replace itself), open the release page with the installers.
api.appUpdate().then(async (release) => {
  if (!release) return;
  const canSelf = await api.canSelfUpdate().catch(() => false);
  const page = () => api.openRepoPage(release.url).catch(() => {});
  let armed = false;
  const btn = h("button", {
    class: "btn update",
    title: canSelf ? "Downloads the update, installs it and restarts the app" : "Opens the release page with the installers",
    onclick: async () => {
      if (!canSelf) return page();
      // Terminals close while the app restarts: ask with a second click, not a blocking dialog.
      if (!armed) {
        armed = true;
        btn.textContent = "Click again: restarts the app";
        setTimeout(() => { if (!btn.disabled) { armed = false; btn.textContent = `Update to ${release.version}`; } }, 5000);
        return;
      }
      btn.disabled = true;
      btn.textContent = "Updating…";
      try {
        await api.installUpdate();
      } catch (e) {
        btn.disabled = false;
        armed = false;
        btn.textContent = `Version ${release.version} available`;
        toast(`${errText(e)}. Opening the release page instead.`, "error");
        page();
      }
    },
  }, canSelf ? `Update to ${release.version}` : `Version ${release.version} available`);
  document.getElementById("settings").before(btn);
}).catch(() => {});
