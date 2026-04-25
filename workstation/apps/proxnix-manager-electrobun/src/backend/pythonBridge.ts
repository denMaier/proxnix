import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
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

function bundledResourceCandidates(): string[] {
  const executableDir = dirname(process.argv0);
  const candidates = [
    resolve(executableDir, "..", "Resources"),
    resolve(executableDir, "Contents", "Resources"),
    resolve(executableDir, "Resources"),
  ];
  return [...new Set(candidates)];
}

function resolvePythonCommand(): string[] {
  const explicit = process.env.PROXNIX_MANAGER_PYTHON?.trim();
  if (explicit) {
    return explicit.split(/\s+/).filter(Boolean);
  }

  for (const resourcesDir of bundledResourceCandidates()) {
    const bundledPython = resolve(resourcesDir, "bin", "proxnix-python");
    if (existsSync(bundledPython)) {
      return [bundledPython];
    }
  }

  const moduleRelative = fileURLToPath(new URL("../../../../.venv/bin/python", import.meta.url));
  if (existsSync(moduleRelative)) {
    return [moduleRelative];
  }

  for (const brew of ["/opt/homebrew/bin/brew", "/usr/local/bin/brew", Bun.which("brew")].filter(
    (candidate): candidate is string => Boolean(candidate),
  )) {
    if (!existsSync(brew)) {
      continue;
    }
    const result = Bun.spawnSync([brew, "--prefix", "python"], { stderr: "pipe", stdout: "pipe" });
    if (result.success) {
      const prefix = new TextDecoder().decode(result.stdout).trim();
      const brewedPython = resolve(prefix, "libexec", "bin", "python3");
      if (existsSync(brewedPython)) {
        return [brewedPython];
      }
    }
  }

  const preferredPaths = ["/opt/homebrew/opt/python@3.12/bin/python3.12", "/usr/local/opt/python@3.12/bin/python3.12"];
  for (const candidate of preferredPaths) {
    if (existsSync(candidate)) {
      return [candidate];
    }
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
  const moduleRelative = fileURLToPath(new URL("./scripts/proxnix_bridge.py", import.meta.url));
  if (existsSync(moduleRelative)) {
    return moduleRelative;
  }

  const bundledPaths = bundledResourceCandidates().flatMap((resourcesDir) => [
    resolve(resourcesDir, "app", "backend", "scripts", "proxnix_bridge.py"),
    resolve(resourcesDir, "backend", "scripts", "proxnix_bridge.py"),
    resolve(resourcesDir, "app", "bun", "scripts", "proxnix_bridge.py"),
    resolve(resourcesDir, "bun", "scripts", "proxnix_bridge.py"),
  ]);
  const bundledPath = bundledPaths.find((candidate) => existsSync(candidate));
  if (bundledPath) {
    return bundledPath;
  }

  throw new Error(
    `Could not find proxnix bridge script. Checked ${moduleRelative} and ${bundledPaths.join(", ")}.`,
  );
}

export function runBridge<T>(command: string, payload?: unknown): T {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([, value]) => value !== undefined),
  ) as Record<string, string>;

  const stdinData =
    payload === undefined ? "ignore" as const : Buffer.from(JSON.stringify(payload), "utf-8");

  const subprocess = Bun.spawnSync(
    [...resolvePythonCommand(), bridgeScriptPath(), command],
    {
      stdin: stdinData,
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
