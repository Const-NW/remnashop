import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@app/App";
import "@shared/i18n";
import { installFormValidationMessages } from "@shared/lib/form-validation";
import { registerServiceWorker } from "@shared/lib/push";
import { readStorage, writeStorage } from "@shared/lib/safe-storage";

import "@app/styles.css";

const CHUNK_RELOAD_KEY = "remnashop_chunk_reload_at";
// a second failure this soon after reloading is a real outage, not a stale tab
const CHUNK_RELOAD_COOLDOWN_MS = 10_000;

// A tab opened before a deploy asks for old hashed chunks that no longer
// exist; a reload picks up the new index.html instead of the route's errorElement.
window.addEventListener("vite:preloadError", (event) => {
  const lastReload = Number(readStorage(CHUNK_RELOAD_KEY, "session"));
  if (Date.now() - lastReload < CHUNK_RELOAD_COOLDOWN_MS) return;
  writeStorage(CHUNK_RELOAD_KEY, String(Date.now()), "session");
  event.preventDefault();
  window.location.reload();
});

// Paddings and masks give back the width the scrollbar really takes: zero for overlay scrollbars
// (Firefox and macOS defaults), the native width where a browser skips the 10px ::-webkit-scrollbar.
const scrollbarProbe = document.createElement("div");
scrollbarProbe.style.cssText = "position:absolute;visibility:hidden;width:100px;height:100px;overflow:scroll";
document.body.append(scrollbarProbe);
document.documentElement.style.setProperty(
  "--scrollbar-size",
  `${scrollbarProbe.offsetWidth - scrollbarProbe.clientWidth}px`,
);
scrollbarProbe.remove();

// iOS zooms in on a field under 16px as it takes focus; maximum-scale stops that, and iOS still lets a pinch zoom
// (Android would lose it, so only here). iPadOS Safari claims to be a Mac: only the touch points tell them apart.
if (
  /iphone|ipad|ipod/i.test(navigator.userAgent) ||
  (/macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1)
) {
  const viewport = document.querySelector('meta[name="viewport"]');
  viewport?.setAttribute("content", `${viewport.getAttribute("content")}, maximum-scale=1`);
}

installFormValidationMessages();
registerServiceWorker();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
