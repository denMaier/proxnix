import { BrowserView, BrowserWindow, Utils } from "electrobun/bun";
import type { ProxnixManagerRPC } from "../shared/types";
import { loadSnapshot, saveConfig } from "./workstationBridge";

const proxnixRpc = BrowserView.defineRPC<ProxnixManagerRPC>({
  maxRequestTime: 15000,
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
