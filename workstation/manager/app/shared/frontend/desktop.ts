import { mountProxnixManager, INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS } from "./index";
import { createDesktopRpcClient } from "./desktopRpcClient";

const root = document.querySelector<HTMLDivElement>("#app");

if (!root) {
  throw new Error("Missing app root");
}

root.innerHTML = `<div class="boot-screen">Starting Proxnix Manager...</div>`;

try {
mountProxnixManager({
  root,
  rpc: createDesktopRpcClient(INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS),
});
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  root.innerHTML = `<div class="boot-screen boot-screen-error">
    <strong>Proxnix Manager failed to start</strong>
    <pre>${escapeHtml(message)}</pre>
  </div>`;
  throw error;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
