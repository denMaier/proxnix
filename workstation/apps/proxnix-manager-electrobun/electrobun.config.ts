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
      entrypoint: "src/bun/index.ts",
      sourcemap: "linked",
    },
    views: {
      mainview: {
        entrypoint: "src/mainview/index.ts",
        sourcemap: "linked",
      },
    },
    copy: {
      "src/mainview/index.html": "views/mainview/index.html",
      "src/mainview/index.css": "views/mainview/index.css",
      "src/mainview/assets": "views/mainview/assets",
      "src/bun/scripts": "bun/scripts",
    },
  },
  scripts: {
    postBuild: "scripts/postbuild-cli-runtime.ts",
    postWrap: "scripts/postwrap-cli-runtime.ts",
  },
} satisfies ElectrobunConfig;
