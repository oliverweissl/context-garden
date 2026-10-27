import "@fontsource/manrope/400.css";
import "@fontsource/manrope/600.css";
import "@fontsource/saira-condensed/600.css";
import "@fontsource/saira-condensed/700.css";
import "@xterm/xterm/css/xterm.css";
import "./style.css";

import { renderHome } from "./views/home.js";
import { renderPlant } from "./views/plant.js";
import { renderGarden } from "./views/garden.js";
import { renderAccounts } from "./views/accounts.js";
import { setCrumbs, h } from "./ui.js";
import { api } from "./api.js";

const app = document.getElementById("app");
let cleanup = null;

async function route() {
  if (cleanup) cleanup();
  cleanup = null;
  // Popups belong to the page they were opened on.
  document.querySelectorAll("dialog.modal").forEach((d) => d.close());
  setCrumbs();
  const [, page, id] = location.hash.replace(/^#/, "").split("/");
  app.className = page === "garden" ? "full" : "";
  app.replaceChildren();
  if (page === "new") cleanup = await renderPlant(app);
  else if (page === "garden" && id) cleanup = await renderGarden(app, decodeURIComponent(id));
  else if (page === "accounts") cleanup = await renderAccounts(app);
  else cleanup = await renderHome(app);
}

window.addEventListener("hashchange", route);
route();

// A newer release on GitHub shows up next to Accounts; offline means no hint.
api.appUpdate().then((release) => {
  if (!release) return;
  document.querySelector(".nav .navlink").before(h("button", {
    class: "btn update", title: "Opens the release page with the installers",
    onclick: () => api.openRepoPage(release.url).catch(() => {}),
  }, `Version ${release.version} available`));
}).catch(() => {});
