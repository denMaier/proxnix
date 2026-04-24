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
  const configValues = managerConfigValues();
  const explicit = (process.env.PROXNIX_MANAGER_PYTHON ?? configValues.PROXNIX_MANAGER_PYTHON ?? "").trim();
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

  const nearestVenvPython = findNearestVenvPython();
  if (nearestVenvPython) {
    return [nearestVenvPython];
  }

  const preferredPaths = [
    "/opt/homebrew/opt/python@3.12/bin/python3.12",
    "/usr/local/opt/python@3.12/bin/python3.12",
  ];
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

function findNearestVenvPython(): string | null {
  const moduleDir = dirname(fileURLToPath(import.meta.url));
  const scriptsDir = managerConfigValues().PROXNIX_SCRIPTS_DIR?.trim();
  const starts = [
    moduleDir,
    dirname(bridgeScriptPath()),
    ...(scriptsDir ? [scriptsDir] : []),
    process.cwd(),
    dirname(process.argv0),
  ];
  const seen = new Set<string>();

  for (const start of starts) {
    let current = resolve(start);
    while (!seen.has(current)) {
      seen.add(current);
      const candidate = resolve(current, ".venv", "bin", "python");
      if (existsSync(candidate)) {
        return candidate;
      }

      const parent = dirname(current);
      if (parent === current) {
        break;
      }
      current = parent;
    }
  }

  return null;
}

function managerConfigValues(): Record<string, string> {
  const configPath = resolve(
    process.env.XDG_CONFIG_HOME?.trim() || resolve(process.env.HOME || "", ".config"),
    "proxnix",
    "config",
  );
  if (!existsSync(configPath)) {
    return {};
  }

  const values: Record<string, string> = {};
  for (const rawLine of readFileSync(configPath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(line);
    if (!match) {
      continue;
    }
    values[match[1]] = unquoteConfigValue(match[2].trim());
  }
  return values;
}

function unquoteConfigValue(value: string): string {
  if (
    (value.startsWith("'") && value.endsWith("'")) ||
    (value.startsWith('"') && value.endsWith('"'))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

function bundledResourceCandidates(): string[] {
  const executableDir = dirname(process.argv0);
  const candidates = [
    resolve(executableDir, "..", "Resources"),
    resolve(executableDir, "Contents", "Resources"),
    resolve(executableDir, "Resources"),
  ];
  return [...new Set(candidates)];
}

function bridgeScriptPath(): string {
  const moduleRelative = fileURLToPath(new URL("./scripts/proxnix_bridge.py", import.meta.url));
  if (existsSync(moduleRelative)) {
    return moduleRelative;
  }

  const bundledPaths = bundledResourceCandidates().flatMap((resourcesDir) => [
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

function commandOutput(stdout: string, stderr: string): string {
  return [stdout.trim(), stderr.trim()].filter(Boolean).join("\n");
}

function gitEnv(): Record<string, string> {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([, value]) => value !== undefined),
  ) as Record<string, string>;
  env.HOME = env.HOME || process.env.HOME || "";
  return env;
}

function gitSiteDir(): string {
  const snapshot = loadSnapshot();
  const siteDir = snapshot.config.siteDir.trim();
  if (!siteDir) {
    throw new Error("Set site directory first.");
  }
  if (!existsSync(siteDir)) {
    throw new Error(`Site directory not found: ${siteDir}`);
  }
  return siteDir;
}

function runGit(siteDir: string, args: string[], timeoutMs = 120_000): { output: string; exitCode: number } {
  try {
    const result = Bun.spawnSync(["git", "-C", siteDir, ...args], {
      stdout: "pipe",
      stderr: "pipe",
      env: gitEnv(),
      timeout: timeoutMs,
    });
    return {
      output: commandOutput(
        result.stdout.toString("utf8"),
        result.stderr.toString("utf8"),
      ),
      exitCode: result.exitCode ?? 1,
    };
  } catch (error) {
    return { output: String(error), exitCode: 1 };
  }
}

function commandResult(output: string, exitCode: number, fallbackSuccess = ""): CommandResult {
  const cleaned = output.trim() || (exitCode === 0 ? fallbackSuccess : "");
  return {
    output: cleaned,
    exitCode,
    error: exitCode === 0 ? "" : cleaned,
  };
}

function ensureGitRepo(siteDir: string): CommandResult | null {
  const result = runGit(siteDir, ["rev-parse", "--is-inside-work-tree"], 30_000);
  if (result.exitCode !== 0) {
    return { output: "", exitCode: 1, error: "Site directory is not a git repository." };
  }
  return null;
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
  const empty: GitStatusResult = {
    isRepo: false,
    branch: "",
    clean: true,
    staged: [],
    unstaged: [],
    untracked: [],
    files: [],
    log: [],
    ahead: 0,
    behind: 0,
    hasRemote: false,
    upstream: "",
    error: "",
  };

  let siteDir: string;
  try {
    siteDir = gitSiteDir();
  } catch (error) {
    return { ...empty, error: String(error instanceof Error ? error.message : error) };
  }

  const repoCheck = runGit(siteDir, ["rev-parse", "--is-inside-work-tree"], 30_000);
  if (repoCheck.exitCode !== 0) {
    return { ...empty, error: "Site directory is not a git repository." };
  }

  const branch = runGit(siteDir, ["branch", "--show-current"], 30_000).output;
  const status = runGit(siteDir, ["status", "--porcelain=v1", "-u"], 30_000).output;
  const log = runGit(siteDir, ["log", "--oneline", "-15"], 30_000).output;
  const upstream = runGit(siteDir, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 30_000);

  const files: { status: string; path: string }[] = [];
  const staged: { status: string; path: string }[] = [];
  const unstaged: { status: string; path: string }[] = [];
  const untracked: { status: string; path: string }[] = [];
  for (const line of status.split(/\r?\n/)) {
    if (line.length < 3) continue;
    const indexFlag = line[0] ?? " ";
    const worktreeFlag = line[1] ?? " ";
    const path = line.slice(3);
    const fileStatus = line.slice(0, 2).trim() || "?";
    files.push({ status: fileStatus, path });
    if (indexFlag === "?") {
      untracked.push({ status: "?", path });
    } else {
      if (indexFlag !== " ") staged.push({ status: indexFlag, path });
      if (worktreeFlag !== " ") unstaged.push({ status: worktreeFlag, path });
    }
  }

  const logEntries = log
    .split(/\r?\n/)
    .map((line) => {
      const [hash, ...messageParts] = line.split(" ");
      return hash && messageParts.length > 0 ? { hash, message: messageParts.join(" ") } : null;
    })
    .filter((entry): entry is { hash: string; message: string } => entry !== null);

  let ahead = 0;
  let behind = 0;
  const hasRemote = upstream.exitCode === 0 && upstream.output.length > 0;
  if (hasRemote) {
    const counts = runGit(siteDir, ["rev-list", "--left-right", "--count", `HEAD...${upstream.output}`], 30_000);
    if (counts.exitCode === 0) {
      const [aheadRaw, behindRaw] = counts.output.split(/\s+/);
      ahead = Number.parseInt(aheadRaw ?? "0", 10) || 0;
      behind = Number.parseInt(behindRaw ?? "0", 10) || 0;
    }
  }

  return {
    isRepo: true,
    branch,
    clean: files.length === 0,
    staged,
    unstaged,
    untracked,
    files,
    log: logEntries,
    ahead,
    behind,
    hasRemote,
    upstream: hasRemote ? upstream.output : "",
    error: "",
  };
}

export function gitAdd(options: { all?: boolean; file?: string }): CommandResult {
  let siteDir: string;
  try {
    siteDir = gitSiteDir();
  } catch (error) {
    return { output: "", exitCode: 1, error: String(error instanceof Error ? error.message : error) };
  }
  const repoError = ensureGitRepo(siteDir);
  if (repoError) return repoError;

  if (options.all) {
    const result = runGit(siteDir, ["add", "-A"]);
    return commandResult(result.output, result.exitCode, "All changes staged.");
  }
  const file = options.file?.trim();
  if (!file) {
    return { output: "", exitCode: 1, error: "Choose a file to add." };
  }
  const result = runGit(siteDir, ["add", "--", file]);
  return commandResult(result.output, result.exitCode, `Staged ${file}.`);
}

export function gitCommit(message: string): CommandResult {
  let siteDir: string;
  try {
    siteDir = gitSiteDir();
  } catch (error) {
    return { output: "", exitCode: 1, error: String(error instanceof Error ? error.message : error) };
  }
  const repoError = ensureGitRepo(siteDir);
  if (repoError) return repoError;

  const trimmed = message.trim();
  if (!trimmed) {
    return { output: "", exitCode: 1, error: "Commit message cannot be empty." };
  }
  const result = runGit(siteDir, ["commit", "-m", trimmed]);
  return commandResult(result.output, result.exitCode);
}

export function gitPush(): CommandResult {
  let siteDir: string;
  try {
    siteDir = gitSiteDir();
  } catch (error) {
    return { output: "", exitCode: 1, error: String(error instanceof Error ? error.message : error) };
  }
  const repoError = ensureGitRepo(siteDir);
  if (repoError) return repoError;

  const result = runGit(siteDir, ["push"], 180_000);
  return commandResult(result.output, result.exitCode, "Pushed successfully.");
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
