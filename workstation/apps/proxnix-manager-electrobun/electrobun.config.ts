import type { ElectrobunConfig } from "electrobun";

const appVersion = process.env.VERSION ?? "0.0.0-dev";

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
      "src/bun/scripts": "bun/scripts",
    },
  },
  scripts: {
    postBuild: "scripts/postbuild-cli-runtime.ts",
    postWrap: "scripts/postwrap-cli-runtime.ts",
  },
} satisfies ElectrobunConfig;
