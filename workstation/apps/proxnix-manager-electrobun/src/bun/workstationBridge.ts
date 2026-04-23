import type { AppSnapshot, ProxnixConfig } from "../shared/types";
import { fileURLToPath } from "node:url";

type BridgeEnvelope<T> =
  | {
      ok: true;
      result: T;
    }
  | {
      ok: false;
      error: string;
    };

function resolvePythonCommand(): string[] {
  const explicit = process.env.PROXNIX_MANAGER_PYTHON?.trim();
  if (explicit) {
    return explicit.split(/\s+/).filter(Boolean);
  }

  const python3 = Bun.which("python3");
  if (python3) {
    return [python3];
  }

  const python = Bun.which("python");
  if (python) {
    return [python];
  }

  const py = Bun.which("py");
  if (py) {
    return [py, "-3"];
  }

  throw new Error(
    "Python 3 was not found on PATH. Set PROXNIX_MANAGER_PYTHON to override the interpreter path.",
  );
}

function bridgeScriptPath(): string {
  return fileURLToPath(new URL("./scripts/proxnix_bridge.py", import.meta.url));
}

function runBridge<T>(command: string, payload?: unknown): T {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([, value]) => value !== undefined),
  ) as Record<string, string>;

  const subprocess = Bun.spawnSync(
    [...resolvePythonCommand(), bridgeScriptPath(), command],
    {
      stdin: payload === undefined ? undefined : JSON.stringify(payload),
      stdout: "pipe",
      stderr: "pipe",
      env,
    },
  );

  const stdout = subprocess.stdout.toString("utf8").trim();
  const stderr = subprocess.stderr.toString("utf8").trim();

  if (subprocess.exitCode !== 0 && !stdout) {
    throw new Error(stderr || `Bridge command failed: ${command}`);
  }

  let envelope: BridgeEnvelope<T>;
  try {
    envelope = JSON.parse(stdout) as BridgeEnvelope<T>;
  } catch (error) {
    throw new Error(
      `Bridge command returned invalid JSON for ${command}: ${stdout || stderr || String(error)}`,
    );
  }

  if (!envelope.ok) {
    throw new Error(envelope.error);
  }

  return envelope.result;
}

export function loadSnapshot(): AppSnapshot {
  return runBridge<AppSnapshot>("snapshot");
}

export function saveConfig(config: ProxnixConfig): AppSnapshot {
  return runBridge<AppSnapshot>("save-config", { config });
}
