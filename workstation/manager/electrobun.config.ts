import type { ElectrobunConfig } from "electrobun";

const appVersion = process.env.VERSION ?? "0.0.0-dev";
const enableMacCodesign = process.env.PROXNIX_MANAGER_MACOS_CODESIGN === "1";
const enableMacNotarize = process.env.PROXNIX_MANAGER_MACOS_NOTARIZE === "1";

export default {
  app: {
    name: "Proxnix Manager",
    identifier: "org.proxnix.manager",
    version: appVersion,
    description: "Cross-platform workstation UI for proxnix",
  },
  runtime: {
    exitOnLastWindowClosed: true,
  },
  build: {
    mac: {
      codesign: enableMacCodesign,
      icons: "assets/proxnix.iconset",
      notarize: enableMacCodesign && enableMacNotarize,
    },
    bun: {
      entrypoint: "app/desktop/index.ts",
      sourcemap: "linked",
    },
    views: {
      mainview: {
        entrypoint: "app/shared/frontend/desktop.ts",
        sourcemap: "linked",
      },
    },
    copy: {
      "app/shared/frontend/index.html": "views/mainview/index.html",
      "app/shared/frontend/index.css": "views/mainview/index.css",
      "app/shared/frontend/assets": "views/mainview/assets",
      "app/shared/capabilities/scripts": "capabilities/scripts",
    },
  },
  scripts: {
    postBuild: "scripts/postbuild-cli-runtime.ts",
    postWrap: "scripts/postwrap-cli-runtime.ts",
  },
} satisfies ElectrobunConfig;
