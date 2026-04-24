import type {
  AppSnapshot,
  CommandResult,
  DoctorResult,
  FilePreview,
  GitStatusResult,
  OnboardingResult,
  ProxnixConfig,
  SecretScopeStatus,
  SecretsProviderStatus,
  SidebarMetadata,
} from "../shared/types";
import { existsSync, readFileSync } from "node:fs";
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

type SecretScopeOptions = {
  scopeType: "shared" | "group" | "container";
  scopeId?: string;
};

const SECRET_CACHE_TTL_MS = 15_000;

const cache: {
  snapshot: AppSnapshot | null;
  providerStatus: { key: string; value: SecretsProviderStatus; loadedAt: number } | null;
  scopeStatuses: Map<string, { value: SecretScopeStatus; loadedAt: number }>;
} = {
  snapshot: null,
  providerStatus: null,
  scopeStatuses: new Map(),
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

function snapshotCacheKey(snapshot: AppSnapshot): string {
  return JSON.stringify({
    siteDir: snapshot.config.siteDir,
    provider: snapshot.config.secretProvider,
    providerCommand: snapshot.config.secretProviderCommand,
  });
}

function currentSecretsCacheKey(): string {
  return cache.snapshot ? snapshotCacheKey(cache.snapshot) : "";
}

function secretScopeCacheKey(options: SecretScopeOptions): string {
  return JSON.stringify({
    secrets: currentSecretsCacheKey(),
    scopeType: options.scopeType,
    scopeId: options.scopeId ?? "",
  });
}

function isFresh(loadedAt: number, ttlMs: number): boolean {
  return Date.now() - loadedAt < ttlMs;
}

function invalidateSecretsCache(): void {
  cache.providerStatus = null;
  cache.scopeStatuses.clear();
}

function setSnapshotCache(snapshot: AppSnapshot, options?: { invalidateSecrets?: boolean }): AppSnapshot {
  const previousKey = cache.snapshot ? snapshotCacheKey(cache.snapshot) : null;
  const nextKey = snapshotCacheKey(snapshot);
  cache.snapshot = snapshot;
  if (options?.invalidateSecrets || previousKey !== nextKey) {
    invalidateSecretsCache();
  }
  return snapshot;
}

function invalidateAllCache(): void {
  cache.snapshot = null;
  invalidateSecretsCache();
}

export function loadSnapshot(options?: { force?: boolean }): AppSnapshot {
  if (!options?.force && cache.snapshot) {
    return cache.snapshot;
  }
  return setSnapshotCache(runBridge<AppSnapshot>("snapshot"), {
    invalidateSecrets: options?.force,
  });
}

export function saveConfig(config: ProxnixConfig): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("save-config", { config }));
}

export function runOnboarding(config: ProxnixConfig): OnboardingResult {
  const result = runBridge<OnboardingResult>("run-onboarding", { config });
  setSnapshotCache(result.snapshot, { invalidateSecrets: true });
  return result;
}

export function createSiteNix(): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("create-site-nix"), {
    invalidateSecrets: false,
  });
}

export function saveSidebarMetadata(vmid: string, metadata: SidebarMetadata): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("save-sidebar-metadata", { vmid, metadata }));
}

export function loadSecretsProviderStatus(options?: { force?: boolean }): SecretsProviderStatus {
  const key = currentSecretsCacheKey();
  if (
    !options?.force &&
    cache.providerStatus &&
    cache.providerStatus.key === key &&
    isFresh(cache.providerStatus.loadedAt, SECRET_CACHE_TTL_MS)
  ) {
    return cache.providerStatus.value;
  }

  const value = runBridge<SecretsProviderStatus>("secrets-provider-status");
  cache.providerStatus = { key, value, loadedAt: Date.now() };
  return value;
}

export function loadSecretScopeStatus(options: {
  scopeType: "shared" | "group" | "container";
  scopeId?: string;
  force?: boolean;
}): SecretScopeStatus {
  const key = secretScopeCacheKey(options);
  const cached = cache.scopeStatuses.get(key);
  if (!options.force && cached && isFresh(cached.loadedAt, SECRET_CACHE_TTL_MS)) {
    return cached.value;
  }

  const value = runBridge<SecretScopeStatus>("secret-scope-status", options);
  cache.scopeStatuses.set(key, { value, loadedAt: Date.now() });
  return value;
}

function invalidateAfterCommand(
  result: CommandResult,
  options: { secrets?: boolean; snapshot?: boolean } = { secrets: true },
): CommandResult {
  if (result.exitCode === 0) {
    if (options.snapshot) {
      invalidateAllCache();
    } else if (options.secrets) {
      invalidateSecretsCache();
    }
  }
  return result;
}

export function setSecret(options: {
  scopeType: "shared" | "group" | "container";
  scopeId?: string;
  name: string;
  value: string;
}): CommandResult {
  return invalidateAfterCommand(runBridge<CommandResult>("set-secret", options));
}

export function removeSecret(options: {
  scopeType: "shared" | "group" | "container";
  scopeId?: string;
  name: string;
}): CommandResult {
  return invalidateAfterCommand(runBridge<CommandResult>("remove-secret", options));
}

export function rotateSecretScope(options: {
  scopeType: "shared" | "group" | "container";
  scopeId?: string;
}): CommandResult {
  return invalidateAfterCommand(runBridge<CommandResult>("rotate-secret-scope", options));
}

export function initContainerIdentity(vmid: string): CommandResult {
  return invalidateAfterCommand(runBridge<CommandResult>("init-container-identity", { vmid }), {
    snapshot: true,
  });
}

export function createContainerBundle(vmid: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("create-container-bundle", { vmid }), {
    invalidateSecrets: true,
  });
}

export function deleteContainerBundle(vmid: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("delete-container-bundle", { vmid }), {
    invalidateSecrets: true,
  });
}

export function createSecretGroup(group: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("create-secret-group", { group }), {
    invalidateSecrets: true,
  });
}

export function deleteSecretGroup(group: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("delete-secret-group", { group }), {
    invalidateSecrets: true,
  });
}

export function attachSecretGroup(vmid: string, group: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("attach-secret-group", { vmid, group }), {
    invalidateSecrets: true,
  });
}

export function detachSecretGroup(vmid: string, group: string): AppSnapshot {
  return setSnapshotCache(runBridge<AppSnapshot>("detach-secret-group", { vmid, group }), {
    invalidateSecrets: true,
  });
}

export function runDoctor(options: { configOnly?: boolean; vmid?: string }): DoctorResult {
  return runBridge<DoctorResult>("run-doctor", options);
}

export function runPublish(options: { dryRun?: boolean; configOnly?: boolean; vmid?: string; hosts?: string[] }): CommandResult {
  return invalidateAfterCommand(runBridge<CommandResult>("run-publish", options), {
    snapshot: !options.dryRun,
    secrets: false,
  });
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

export function readTextFile(path: string): FilePreview {
  return {
    path,
    content: readFileSync(path, "utf-8"),
  };
}
