import { BrowserView, BrowserWindow, Utils } from "electrobun/bun";
import type { ProxnixManagerRPC } from "../shared/types";
import {
  gitAdd,
  gitCommit,
  gitPush,
  gitStatus,
  loadSecretsProviderStatus,
  loadSnapshot,
  runDoctor,
  runPublish,
  saveConfig,
  saveSidebarMetadata,
} from "./workstationBridge";

const INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS = 60 * 60 * 1000;

const proxnixRpc = BrowserView.defineRPC<ProxnixManagerRPC>({
  maxRequestTime: INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS,
  handlers: {
    requests: {
      loadSnapshot: (_params: void) => loadSnapshot(),
      saveConfig: (params) => saveConfig(params.config),
      chooseSiteDirectory: async (params) => {
        const startingFolder = params?.startingFolder;
        const chosenPaths = await Utils.openFileDialog({
          startingFolder:
            startingFolder && startingFolder.trim().length > 0
              ? startingFolder
              : Utils.paths.home,
          allowedFileTypes: "*",
          canChooseFiles: false,
          canChooseDirectory: true,
          allowsMultipleSelection: false,
        });

        return chosenPaths?.[0] ?? null;
      },
      openPath: (params) => Utils.openPath(params.path),
      saveSidebarMetadata: (params) => saveSidebarMetadata(params.vmid, params.metadata),
      loadSecretsProviderStatus: (_params: void) => loadSecretsProviderStatus(),
      runDoctor: (params) => runDoctor(params),
      runPublish: (params) => runPublish(params),
      gitStatus: (_params: void) => gitStatus(),
      gitAdd: (params) => gitAdd(params),
      gitCommit: (params) => gitCommit(params.message),
      gitPush: (_params: void) => gitPush(),
      openInEditor: async (params) => {
        const editors = ["code", "cursor", "zed", "subl"];
        for (const editor of editors) {
          try {
            const proc = Bun.spawn([editor, params.path], { stdout: "ignore", stderr: "ignore" });
            await proc.exited;
            if (proc.exitCode === 0) {
              return { opened: true, editor };
            }
          } catch {
            // editor not found, try next
          }
        }
        // Fall back to OS default text editor
        const fallback = process.platform === "darwin"
          ? ["open", "-t", params.path]
          : ["xdg-open", params.path];
        try {
          const proc = Bun.spawn(fallback, { stdout: "ignore", stderr: "ignore" });
          await proc.exited;
          if (proc.exitCode === 0) {
            return { opened: true, editor: "default" };
          }
        } catch {
          // ignore
        }
        return { opened: false, error: `No editor found for: ${params.path}` };
      },
    },
    messages: {},
  },
});

new BrowserWindow({
  title: "Proxnix Manager",
  url: "views://mainview/index.html",
  rpc: proxnixRpc,
  frame: {
    width: 1480,
    height: 920,
    x: 120,
    y: 80,
  },
  titleBarStyle: "default",
});
