import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const shellScript = join(scriptDir, "postwrap-cli-runtime.sh");
const bundlePath = process.env.ELECTROBUN_WRAPPER_BUNDLE_PATH;

if (!bundlePath) {
  console.error("ELECTROBUN_WRAPPER_BUNDLE_PATH is required");
  process.exit(1);
}

const result = spawnSync("/bin/bash", [shellScript, bundlePath], {
  stdio: ["inherit", "inherit", "inherit"],
  env: process.env,
});

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
