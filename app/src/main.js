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
import { setCrumbs } from "./ui.js";

const app = document.getElementById("app");
let cleanup = null;

async function route() {
  if (cleanup) cleanup();
  cleanup = null;
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
