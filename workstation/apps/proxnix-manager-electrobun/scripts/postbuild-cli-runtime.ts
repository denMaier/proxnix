import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const targetOs = process.env.ELECTROBUN_OS;
if (targetOs !== "linux") {
  process.exit(0);
}

const buildDir = process.env.ELECTROBUN_BUILD_DIR;
const appName = process.env.ELECTROBUN_APP_NAME;

if (!buildDir || !appName) {
  console.error("ELECTROBUN_BUILD_DIR and ELECTROBUN_APP_NAME are required for Linux postBuild");
  process.exit(1);
}

const scriptDir = dirname(fileURLToPath(import.meta.url));
const shellScript = join(scriptDir, "postwrap-cli-runtime.sh");
const bundlePath = join(buildDir, appName);

const result = spawnSync("/bin/bash", [shellScript, bundlePath], {
  stdio: ["inherit", "inherit", "inherit"],
  env: process.env,
});

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
