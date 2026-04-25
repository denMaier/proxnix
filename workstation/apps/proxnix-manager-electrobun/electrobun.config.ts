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
      entrypoint: "src/desktop-shell/index.ts",
      sourcemap: "linked",
    },
    views: {
      localFrontend: {
        entrypoint: "src/frontend/index.ts",
        sourcemap: "linked",
      },
    },
    copy: {
      "src/frontend/index.html": "views/localFrontend/index.html",
      "src/frontend/index.css": "views/localFrontend/index.css",
      "src/frontend/assets": "views/localFrontend/assets",
      "src/backend/scripts": "backend/scripts",
    },
  },
  scripts: {
    postBuild: "scripts/postbuild-cli-runtime.ts",
    postWrap: "scripts/postwrap-cli-runtime.ts",
  },
} satisfies ElectrobunConfig;
