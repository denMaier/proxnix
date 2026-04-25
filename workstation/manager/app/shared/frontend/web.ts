import { mountProxnixManager } from "./index";
import { createWebRpcClient } from "./webRpcClient";

const root = document.querySelector<HTMLDivElement>("#app");

if (!root) {
  throw new Error("Missing app root");
}

mountProxnixManager({
  root,
  rpc: createWebRpcClient(),
});
