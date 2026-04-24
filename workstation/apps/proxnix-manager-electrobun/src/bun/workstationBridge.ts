import type {
  AppSnapshot,
  CommandResult,
  DoctorResult,
  GitStatusResult,
  ProxnixConfig,
  SecretsProviderStatus,
  SidebarMetadata,
} from "../shared/types";
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
  const moduleRelative = fileURLToPath(new URL("./scripts/proxnix_bridge.py", import.meta.url));
  if (existsSync(moduleRelative)) {
    return moduleRelative;
  }

  const bundledPath = resolve(
    dirname(process.argv0),
    "..",
    "Resources",
    "app",
    "bun",
    "scripts",
    "proxnix_bridge.py",
  );
  if (existsSync(bundledPath)) {
    return bundledPath;
  }

  throw new Error(
    `Could not find proxnix bridge script. Checked ${moduleRelative} and ${bundledPath}.`,
  );
}

function runBridge<T>(command: string, payload?: unknown): T {
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

export function loadSnapshot(): AppSnapshot {
  return runBridge<AppSnapshot>("snapshot");
}

export function saveConfig(config: ProxnixConfig): AppSnapshot {
  return runBridge<AppSnapshot>("save-config", { config });
}

export function saveSidebarMetadata(vmid: string, metadata: SidebarMetadata): AppSnapshot {
  return runBridge<AppSnapshot>("save-sidebar-metadata", { vmid, metadata });
}

export function loadSecretsProviderStatus(): SecretsProviderStatus {
  return runBridge<SecretsProviderStatus>("secrets-provider-status");
}

export function runDoctor(options: { configOnly?: boolean; vmid?: string }): DoctorResult {
  return runBridge<DoctorResult>("run-doctor", options);
}

export function runPublish(options: { dryRun?: boolean; configOnly?: boolean; vmid?: string; hosts?: string[] }): CommandResult {
  return runBridge<CommandResult>("run-publish", options);
}

export function gitStatus(): GitStatusResult {
  return runBridge<GitStatusResult>("git-status");
}

export function gitAdd(options: { all?: boolean; file?: string }): CommandResult {
  return runBridge<CommandResult>("git-add", options);
}

export function gitCommit(message: string): CommandResult {
  return runBridge<CommandResult>("git-commit", { message });
}

export function gitPush(): CommandResult {
  return runBridge<CommandResult>("git-push");
}

export function openInEditor(path: string): { opened: boolean; editor?: string; error?: string } {
  return runBridge("open-in-editor", { path });
}
