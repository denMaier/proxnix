import { Electroview } from "electrobun/view";
import type {
  AppSnapshot,
  CommandResult,
  FilePreview,
  ContainerSummary,
  DoctorResult,
  GitFile,
  GitStatusResult,
  ProxnixConfig,
  ProxnixManagerRPC,
  SecretScopeStatus,
  SecretsProviderStatus,
  SidebarMetadata,
} from "../shared/types";

type ViewSelection =
  | "welcome"
  | "site"
  | "settings"
  | "publish"
  | "secrets"
  | "secrets:groups"
  | "secrets:containers"
  | `secrets:group:${string}`
  | `secrets:container:${string}`
  | "doctor"
  | "git"
  | `container:${string}`;

type IconName =
  | "back"
  | "box"
  | "branch"
  | "chevron"
  | "edit"
  | "folder"
  | "gear"
  | "health"
  | "home"
  | "key"
  | "lock"
  | "open"
  | "publish"
  | "refresh"
  | "spark"
  | "trash";

const SECRET_PROVIDER_OPTIONS = [
  "embedded-sops",
  "pass",
  "gopass",
  "passhole",
  "pykeepass",
  "onepassword",
  "onepassword-cli",
  "bitwarden",
  "bitwarden-cli",
  "keepassxc",
  "exec",
];

const INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS = 60 * 60 * 1000;
const SECRET_STATUS_FRESH_MS = 15_000;

const proxnixRpc = Electroview.defineRPC<ProxnixManagerRPC>({
  maxRequestTime: INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS,
  handlers: {
    requests: {},
    messages: {},
  },
});

new Electroview({ rpc: proxnixRpc });

const appRoot = document.querySelector<HTMLDivElement>("#app");

if (!appRoot) {
  throw new Error("Missing app root");
}

const root: HTMLDivElement = appRoot;

const state: {
  snapshot: AppSnapshot | null;
  draft: ProxnixConfig | null;
  containerMetadataDraft: SidebarMetadata | null;
  expandedGroups: Set<string>;
  expandedGroupsInitialized: boolean;
  selection: ViewSelection;
  loading: boolean;
  saving: boolean;
  metadataSaving: boolean;
  displaySettingsOpen: boolean;
  sidebarGroupInput: string;
  sidebarLabelInput: string;
  error: string | null;
  doctorResult: DoctorResult | null;
  doctorRunning: boolean;
  doctorConfigOnly: boolean;
  doctorVmid: string;
  publishResult: CommandResult | null;
  publishRunning: boolean;
  publishConfigOnly: boolean;
  publishVmid: string;
  gitResult: GitStatusResult | null;
  gitLoading: boolean;
  gitRunning: boolean;
  gitCommandResult: CommandResult | null;
  gitCommitMessage: string;
  onboardingRunning: boolean;
  onboardingResult: CommandResult | null;
  secretsProviderStatus: SecretsProviderStatus | null;
  secretsProviderLoading: boolean;
  secretsProviderError: string | null;
  secretsProviderLoadedKey: string | null;
  secretsProviderLoadedAt: number;
  secretsProviderLoadingKey: string | null;
  secretsProviderLoadPromise: Promise<void> | null;
  secretScopeStatus: SecretScopeStatus | null;
  secretScopeLoading: boolean;
  secretScopeLoadedKey: string | null;
  secretScopeLoadedAt: number;
  secretScopeError: string | null;
  secretScopeRunning: boolean;
  secretScopeResult: CommandResult | null;
  secretDraftName: string;
  secretDraftValue: string;
  secretGroupDraftName: string;
  containerSecretGroupDraft: string;
  containerBundleDraftVmid: string;
  containerBundleRunning: boolean;
  containerBundleResult: CommandResult | null;
  pendingDeleteContainerBundleVmid: string | null;
  siteNixResult: CommandResult | null;
  secretTopologyRunning: boolean;
  secretTopologyResult: CommandResult | null;
  filePreview: (FilePreview & { title: string }) | null;
  filePreviewLoading: boolean;
} = {
  snapshot: null,
  draft: null,
  containerMetadataDraft: null,
  expandedGroups: new Set<string>(),
  expandedGroupsInitialized: false,
  selection: "welcome",
  loading: true,
  saving: false,
  metadataSaving: false,
  displaySettingsOpen: false,
  sidebarGroupInput: "",
  sidebarLabelInput: "",
  error: null,
  doctorResult: null,
  doctorRunning: false,
  doctorConfigOnly: false,
  doctorVmid: "",
  publishResult: null,
  publishRunning: false,
  publishConfigOnly: false,
  publishVmid: "",
  gitResult: null,
  gitLoading: false,
  gitRunning: false,
  gitCommandResult: null,
  gitCommitMessage: "",
  onboardingRunning: false,
  onboardingResult: null,
  secretsProviderStatus: null,
  secretsProviderLoading: false,
  secretsProviderError: null,
  secretsProviderLoadedKey: null,
  secretsProviderLoadedAt: 0,
  secretsProviderLoadingKey: null,
  secretsProviderLoadPromise: null,
  secretScopeStatus: null,
  secretScopeLoading: false,
  secretScopeLoadedKey: null,
  secretScopeLoadedAt: 0,
  secretScopeError: null,
  secretScopeRunning: false,
  secretScopeResult: null,
  secretDraftName: "",
  secretDraftValue: "",
  secretGroupDraftName: "",
  containerSecretGroupDraft: "",
  containerBundleDraftVmid: "",
  containerBundleRunning: false,
  containerBundleResult: null,
  pendingDeleteContainerBundleVmid: null,
  siteNixResult: null,
  secretTopologyRunning: false,
  secretTopologyResult: null,
  filePreview: null,
  filePreviewLoading: false,
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function defaultConfig(): ProxnixConfig {
  return {
    siteDir: "",
    sopsMasterIdentity: "",
    hosts: "",
    sshIdentity: "",
    remoteDir: "/var/lib/proxnix",
    remotePrivDir: "/var/lib/proxnix/private",
    remoteHostRelayIdentity: "/etc/proxnix/host_relay_identity",
    secretProvider: "embedded-sops",
    secretProviderCommand: "",
    scriptsDir: "",
    managerPythonPath: "",
  };
}

function cloneConfig(config: ProxnixConfig): ProxnixConfig {
  return { ...config };
}

function defaultSidebarMetadata(): SidebarMetadata {
  return {
    displayName: "",
    group: "",
    labels: [],
  };
}

function cloneSidebarMetadata(metadata: SidebarMetadata): SidebarMetadata {
  return {
    displayName: metadata.displayName,
    group: metadata.group,
    labels: [...metadata.labels],
  };
}

function normalizeString(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const entry of value) {
    const trimmed = normalizeString(entry).trim();
    if (!trimmed) {
      continue;
    }
    const key = trimmed.toLocaleLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    normalized.push(trimmed);
  }
  return normalized;
}

function normalizeConfig(config: Partial<ProxnixConfig> | null | undefined): ProxnixConfig {
  const base = defaultConfig();
  return {
    siteDir: normalizeString(config?.siteDir ?? base.siteDir),
    sopsMasterIdentity: normalizeString(config?.sopsMasterIdentity ?? base.sopsMasterIdentity),
    hosts: normalizeString(config?.hosts ?? base.hosts),
    sshIdentity: normalizeString(config?.sshIdentity ?? base.sshIdentity),
    remoteDir: normalizeString(config?.remoteDir ?? base.remoteDir),
    remotePrivDir: normalizeString(config?.remotePrivDir ?? base.remotePrivDir),
    remoteHostRelayIdentity: normalizeString(
      config?.remoteHostRelayIdentity ?? base.remoteHostRelayIdentity,
    ),
    secretProvider: normalizeString(config?.secretProvider ?? base.secretProvider) || base.secretProvider,
    secretProviderCommand: normalizeString(config?.secretProviderCommand ?? base.secretProviderCommand),
    scriptsDir: normalizeString(config?.scriptsDir ?? base.scriptsDir),
    managerPythonPath: normalizeString(config?.managerPythonPath ?? base.managerPythonPath),
  };
}

function normalizeSidebarMetadata(metadata: Partial<SidebarMetadata> | null | undefined): SidebarMetadata {
  return {
    displayName: normalizeString(metadata?.displayName).trim(),
    group: normalizeString(metadata?.group).trim(),
    labels: normalizeStringList(metadata?.labels),
  };
}

function usesEmbeddedSops(provider: string): boolean {
  return provider.trim() === "embedded-sops";
}

function usesExecProvider(provider: string): boolean {
  return provider.trim() === "exec";
}

function defaultMasterIdentityPath(): string {
  return "~/.ssh/proxnix-master";
}

function onboardingConfig(): ProxnixConfig {
  const draft = normalizeConfig(state.draft ?? state.snapshot?.config ?? defaultConfig());
  if (usesEmbeddedSops(draft.secretProvider) && !draft.sopsMasterIdentity.trim()) {
    draft.sopsMasterIdentity = defaultMasterIdentityPath();
  }
  return draft;
}

function canRunOnboarding(config: ProxnixConfig): boolean {
  if (!config.siteDir.trim() || state.onboardingRunning) {
    return false;
  }
  if (usesEmbeddedSops(config.secretProvider) && !config.sopsMasterIdentity.trim()) {
    return false;
  }
  if (usesExecProvider(config.secretProvider) && !config.secretProviderCommand.trim()) {
    return false;
  }
  return true;
}

function normalizeSnapshot(snapshot: AppSnapshot): AppSnapshot {
  const sidebarMetadata = Object.fromEntries(
    Object.entries(snapshot.sidebarMetadata ?? {}).map(([vmid, metadata]) => [
      normalizeString(vmid),
      normalizeSidebarMetadata(metadata),
    ]),
  );

  return {
    configPath: normalizeString(snapshot.configPath),
    configExists: Boolean(snapshot.configExists),
    siteDirExists: Boolean(snapshot.siteDirExists),
    siteNixPath: normalizeString(snapshot.siteNixPath),
    siteNixExists: Boolean(snapshot.siteNixExists),
    siteNixContent: normalizeString(snapshot.siteNixContent),
    preservedConfigKeys: normalizeStringList(snapshot.preservedConfigKeys),
    warnings: normalizeStringList(snapshot.warnings),
    config: normalizeConfig(snapshot.config),
    containers: (snapshot.containers ?? []).map((container) => ({
      vmid: normalizeString(container.vmid),
      containerPath: normalizeString(container.containerPath),
      privateContainerPath: normalizeString(container.privateContainerPath),
      dropins: normalizeStringList(container.dropins),
      hasConfig: Boolean(container.hasConfig),
      hasIdentity: Boolean(container.hasIdentity),
      secretGroups: normalizeStringList(container.secretGroups),
    })),
    definedSecretGroups: normalizeStringList(snapshot.definedSecretGroups),
    attachedSecretGroups: normalizeStringList(snapshot.attachedSecretGroups),
    sidebarMetadata,
  };
}

function normalizeSecretsProviderStatus(status: SecretsProviderStatus): SecretsProviderStatus {
  const containerIdentities: Record<string, boolean> = {};
  for (const [vmid, hasIdentity] of Object.entries(status.containerIdentities ?? {})) {
    const normalizedVmid = normalizeString(vmid).trim();
    if (normalizedVmid) {
      containerIdentities[normalizedVmid] = Boolean(hasIdentity);
    }
  }

  return {
    provider: normalizeString(status.provider),
    definedSecretGroups: normalizeStringList(status.definedSecretGroups),
    containerIdentities,
    warnings: normalizeStringList(status.warnings),
  };
}

function isDirty(): boolean {
  if (!state.snapshot || !state.draft) {
    return false;
  }
  return JSON.stringify(state.snapshot.config) !== JSON.stringify(state.draft);
}

function selectedContainer(): ContainerSummary | null {
  if (!state.snapshot || !state.selection.startsWith("container:")) {
    return null;
  }
  const vmid = state.selection.slice("container:".length);
  return state.snapshot.containers.find((container) => container.vmid === vmid) ?? null;
}

function parseSidebarLabels(rawValue: string): string[] {
  return normalizeStringList(rawValue.split(/,|\n/u));
}

function sidebarMetadataFor(snapshot: AppSnapshot, vmid: string): SidebarMetadata {
  return normalizeSidebarMetadata(snapshot.sidebarMetadata[vmid]);
}

function sidebarTitleFor(snapshot: AppSnapshot, container: ContainerSummary): string {
  const metadata = sidebarMetadataFor(snapshot, container.vmid);
  return metadata.displayName || `VMID ${container.vmid}`;
}

function effectiveContainerHasIdentity(container: ContainerSummary): boolean {
  return state.secretsProviderStatus?.containerIdentities[container.vmid] ?? container.hasIdentity;
}

function currentContainerSidebarMetadata(snapshot: AppSnapshot, vmid: string): SidebarMetadata {
  return cloneSidebarMetadata(sidebarMetadataFor(snapshot, vmid));
}

function syncContainerMetadataDraft(snapshot: AppSnapshot): void {
  const container = selectedContainer();
  state.containerMetadataDraft = container
    ? currentContainerSidebarMetadata(snapshot, container.vmid)
    : null;
  state.sidebarGroupInput = "";
  state.sidebarLabelInput = "";
}

function secretsProviderCacheKey(snapshot: AppSnapshot): string {
  return JSON.stringify({
    siteDir: snapshot.config.siteDir,
    provider: snapshot.config.secretProvider,
    providerCommand: snapshot.config.secretProviderCommand,
    containers: snapshot.containers.map((container) => container.vmid),
    attachedGroups: snapshot.attachedSecretGroups,
  });
}

function clearSecretsProviderStatus(): void {
  state.secretsProviderStatus = null;
  state.secretsProviderError = null;
  state.secretsProviderLoadedKey = null;
  state.secretsProviderLoadedAt = 0;
  state.secretsProviderLoadingKey = null;
  state.secretsProviderLoadPromise = null;
  state.secretsProviderLoading = false;
}

function isFreshTimestamp(loadedAt: number): boolean {
  return loadedAt > 0 && Date.now() - loadedAt < SECRET_STATUS_FRESH_MS;
}

function secretScopeFromSelection(): { scopeType: "shared" | "group" | "container"; scopeId: string } | null {
  if (state.selection === "secrets:group:shared") {
    return { scopeType: "shared", scopeId: "shared" };
  }
  if (state.selection.startsWith("secrets:group:")) {
    return { scopeType: "group", scopeId: state.selection.slice("secrets:group:".length) };
  }
  if (state.selection.startsWith("secrets:container:")) {
    return { scopeType: "container", scopeId: state.selection.slice("secrets:container:".length) };
  }
  if (state.selection.startsWith("container:")) {
    return { scopeType: "container", scopeId: state.selection.slice("container:".length) };
  }
  return null;
}

function isSecretsIndexSelection(): boolean {
  return state.selection === "secrets" ||
    state.selection === "secrets:groups" ||
    state.selection === "secrets:containers";
}

function secretScopeCacheKey(): string | null {
  const scope = secretScopeFromSelection();
  if (!scope || !state.snapshot) {
    return null;
  }
  return JSON.stringify({
    siteDir: state.snapshot.config.siteDir,
    provider: state.snapshot.config.secretProvider,
    providerCommand: state.snapshot.config.secretProviderCommand,
    scope,
  });
}

function clearSecretScopeStatus(): void {
  state.secretScopeStatus = null;
  state.secretScopeLoadedKey = null;
  state.secretScopeLoadedAt = 0;
  state.secretScopeError = null;
  state.secretScopeResult = null;
  state.secretScopeLoading = false;
}

async function ensureSecretScopeStatus(force = false): Promise<void> {
  const scope = secretScopeFromSelection();
  const key = secretScopeCacheKey();
  if (!scope || !key) {
    clearSecretScopeStatus();
    return;
  }
  const hasCachedStatus = state.secretScopeLoadedKey === key && state.secretScopeStatus;
  if (!force && hasCachedStatus && isFreshTimestamp(state.secretScopeLoadedAt)) {
    return;
  }

  const backgroundRefresh = !force && Boolean(hasCachedStatus);
  state.secretScopeLoading = !backgroundRefresh;
  state.secretScopeError = null;
  if (!backgroundRefresh) {
    render();
  }
  try {
    state.secretScopeStatus = await proxnixRpc.request.loadSecretScopeStatus({
      scopeType: scope.scopeType,
      scopeId: scope.scopeType === "shared" ? undefined : scope.scopeId,
      force: force || backgroundRefresh,
    });
    state.secretScopeLoadedKey = key;
    state.secretScopeLoadedAt = Date.now();
  } catch (error) {
    state.secretScopeError = error instanceof Error ? error.message : String(error);
    if (!hasCachedStatus) {
      state.secretScopeStatus = null;
    }
  } finally {
    state.secretScopeLoading = false;
    render();
  }
}

function ensureSecretsProviderStatus(force = false): Promise<void> | null {
  const snapshot = state.snapshot;
  if (!snapshot || snapshot.config.siteDir.length === 0) {
    return null;
  }

  const key = secretsProviderCacheKey(snapshot);
  const hasCachedStatus = state.secretsProviderLoadedKey === key && state.secretsProviderStatus;
  if (!force && hasCachedStatus && isFreshTimestamp(state.secretsProviderLoadedAt)) {
    return null;
  }
  if (!force && state.secretsProviderLoadPromise && state.secretsProviderLoadingKey === key) {
    return state.secretsProviderLoadPromise;
  }

  const backgroundRefresh = !force && Boolean(hasCachedStatus);
  state.secretsProviderLoading = !backgroundRefresh;
  state.secretsProviderLoadingKey = key;
  state.secretsProviderError = null;
  if (!backgroundRefresh) {
    render();
  }

  state.secretsProviderLoadPromise = (async () => {
    try {
      const status = normalizeSecretsProviderStatus(
        await proxnixRpc.request.loadSecretsProviderStatus({ force: force || backgroundRefresh }),
      );
      if (state.snapshot && secretsProviderCacheKey(state.snapshot) === key) {
        state.secretsProviderStatus = status;
        state.secretsProviderLoadedKey = key;
        state.secretsProviderLoadedAt = Date.now();
      }
    } catch (error) {
      if (state.snapshot && secretsProviderCacheKey(state.snapshot) === key) {
        state.secretsProviderError = error instanceof Error ? error.message : String(error);
      }
    } finally {
      if (state.snapshot && secretsProviderCacheKey(state.snapshot) === key) {
        state.secretsProviderLoading = false;
        state.secretsProviderLoadingKey = null;
        state.secretsProviderLoadPromise = null;
      }
      render();
    }
  })();

  return state.secretsProviderLoadPromise;
}

function setSelection(next: ViewSelection): void {
  state.selection = next;
  state.pendingDeleteContainerBundleVmid = null;
  if (state.snapshot) {
    syncContainerMetadataDraft(state.snapshot);
  }
  if (!next.startsWith("container:")) {
    state.displaySettingsOpen = false;
  }
  render();
  if (next === "git" && !state.gitResult && !state.gitLoading) {
    void handleRefreshGit();
  }
  if (next === "secrets" || next.startsWith("secrets:") || next.startsWith("container:")) {
    void ensureSecretsProviderStatus();
  }
  if (next.startsWith("secrets:") || next.startsWith("container:")) {
    void ensureSecretScopeStatus();
  } else {
    clearSecretScopeStatus();
  }
}

function ensureSelection(snapshot: AppSnapshot): void {
  if (snapshot.config.siteDir.length === 0) {
    if (state.selection !== "settings") {
      state.selection = "welcome";
    }
    state.containerMetadataDraft = null;
    state.displaySettingsOpen = false;
    return;
  }

  if (state.selection.startsWith("container:")) {
    const vmid = state.selection.slice("container:".length);
    if (snapshot.containers.some((container) => container.vmid === vmid)) {
      return;
    }
  }

  if (
    state.selection === "settings" ||
    state.selection === "site" ||
    state.selection === "welcome" ||
    state.selection === "publish" ||
    state.selection === "secrets" ||
    state.selection === "secrets:groups" ||
    state.selection === "secrets:containers" ||
    state.selection.startsWith("secrets:group:") ||
    state.selection.startsWith("secrets:container:") ||
    state.selection === "doctor" ||
    state.selection === "git"
  ) {
    state.containerMetadataDraft = null;
    state.displaySettingsOpen = false;
    return;
  }

  state.selection = "welcome";
  state.containerMetadataDraft = null;
  state.displaySettingsOpen = false;
}

function escapeHtml(value: unknown): string {
  return normalizeString(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function compactPath(path: string): string {
  return path
    .replace(/^\/Users\/[^/]+(?=\/)/, "~")
    .replace(/^\/home\/[^/]+(?=\/)/, "~");
}

function icon(name: IconName): string {
  const paths: Record<IconName, string> = {
    back: '<path d="M19 12H5" /><path d="m12 5-7 7 7 7" />',
    box: '<path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z" /><path d="M3 7.5V16.5L12 21L21 16.5V7.5" /><path d="M12 12v9" />',
    branch:
      '<circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="18" cy="18" r="2.5" /><path d="M8.5 6H15.5" /><path d="M18 8.5V15.5" /><path d="M8.5 6V10.5C8.5 12.71 10.29 14.5 12.5 14.5H18" />',
    chevron: '<path d="m9 6 6 6-6 6" />',
    edit:
      '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5Z" />',
    folder:
      '<path d="M3 8a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8Z" /><path d="M3 10h18" />',
    gear:
      '<circle cx="12" cy="12" r="3.5" /><path d="M12 2.8v2.3M12 18.9v2.3M4.3 7.1l2 1.2M17.7 14.7l2 1.2M2.8 12h2.3M18.9 12h2.3M4.3 16.9l2-1.2M17.7 9.3l2-1.2" />',
    health:
      '<path d="M12 5v14M5 12h14" /><circle cx="12" cy="12" r="9" />',
    home:
      '<path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10.5V20h13v-9.5" /><path d="M9.5 20v-5h5v5" />',
    key:
      '<circle cx="8" cy="12" r="4" /><path d="M12 12h9" /><path d="M17 12v3" /><path d="M20 12v2" />',
    lock:
      '<rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7.5A4 4 0 0 1 12 3.5A4 4 0 0 1 16 7.5V10" />',
    open: '<path d="M14 4h6v6" /><path d="M10 14L20 4" /><path d="M20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5" />',
    publish: '<path d="M12 16V4" /><path d="M7 9l5-5 5 5" /><path d="M4 20h16" />',
    refresh:
      '<path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M7 17a7 7 0 0 0 11-3" /><path d="M17 7A7 7 0 0 0 6 10" />',
    spark:
      '<path d="M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z" />',
    trash:
      '<path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M6 6l1 15h10l1-15" /><path d="M10 10v7" /><path d="M14 10v7" />',
  };

  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]}</svg>`;
}

function renderNavItem(
  iconName: IconName,
  label: string,
  selection: ViewSelection,
  current: ViewSelection,
  extra: string,
): string {
  const active =
    current === selection || (selection === "secrets" && current.startsWith("secrets:"))
      ? " active"
      : "";
  return `
    <button class="nav-item${active}" data-nav="${selection}">
      ${icon(iconName)}
      <span class="nav-item-title">${escapeHtml(label)}</span>
      ${extra}
    </button>
  `;
}

type SidebarContainerGroup = {
  id: string;
  title: string;
  containers: ContainerSummary[];
  isPrimary: boolean;
};

function compareContainers(snapshot: AppSnapshot, left: ContainerSummary, right: ContainerSummary): number {
  const titleComparison = sidebarTitleFor(snapshot, left).localeCompare(sidebarTitleFor(snapshot, right), undefined, {
    sensitivity: "accent",
    numeric: true,
  });
  if (titleComparison !== 0) {
    return titleComparison;
  }
  return left.vmid.localeCompare(right.vmid, undefined, { numeric: true });
}

function sidebarContainerDetail(snapshot: AppSnapshot, container: ContainerSummary): string {
  const metadata = sidebarMetadataFor(snapshot, container.vmid);
  const parts: string[] = [];

  if (metadata.displayName) {
    parts.push(`VMID ${container.vmid}`);
  }

  if (metadata.labels.length > 0) {
    const preview = metadata.labels.slice(0, 2).join(", ");
    parts.push(
      metadata.labels.length > 2 ? `${preview} +${metadata.labels.length - 2}` : preview,
    );
  }

  return parts.join(" • ");
}

function sidebarGroups(snapshot: AppSnapshot): SidebarContainerGroup[] {
  if (snapshot.containers.length === 0) {
    return [];
  }

  const grouped = new Map<string, ContainerSummary[]>();
  for (const container of snapshot.containers) {
    const group = sidebarMetadataFor(snapshot, container.vmid).group;
    const bucket = grouped.get(group) ?? [];
    bucket.push(container);
    grouped.set(group, bucket);
  }

  const hasCustomGroups = [...grouped.keys()].some((key) => key.length > 0);
  const orderedKeys = [...grouped.keys()].sort((left, right) => {
    if (left.length === 0 && right.length === 0) {
      return 0;
    }
    if (left.length === 0) {
      return 1;
    }
    if (right.length === 0) {
      return -1;
    }
    return left.localeCompare(right, undefined, { sensitivity: "accent", numeric: true });
  });

  return orderedKeys.map((key) => ({
    id: key || "_ungrouped",
    title: key || (hasCustomGroups ? "Ungrouped" : "Containers"),
    containers: [...(grouped.get(key) ?? [])].sort((left, right) => compareContainers(snapshot, left, right)),
    isPrimary: key.length === 0 && !hasCustomGroups,
  }));
}

function syncExpandedGroups(snapshot: AppSnapshot): void {
  const nextGroups = sidebarGroups(snapshot);
  const nextIds = new Set(nextGroups.map((group) => group.id));

  if (!state.expandedGroupsInitialized) {
    state.expandedGroups = new Set<string>();
    state.expandedGroupsInitialized = true;
    return;
  }

  for (const id of [...state.expandedGroups]) {
    if (!nextIds.has(id)) {
      state.expandedGroups.delete(id);
    }
  }
}

function renderSidebar(snapshot: AppSnapshot): string {
  const containerGroups = sidebarGroups(snapshot);
  const canCreateBundle = snapshot.config.siteDir.length > 0 && snapshot.siteDirExists;
  const containerButtons =
    containerGroups.length > 0
      ? containerGroups
          .map((group) => {
            const headingClass = group.isPrimary ? "nav-group-heading primary" : "nav-group-heading";
            const items = group.containers
              .map((container) => {
                const selection = `container:${container.vmid}` as ViewSelection;
                const active = state.selection === selection ? " active" : "";
                const detail = sidebarContainerDetail(snapshot, container);

                return `
                  <button class="nav-item container-nav-item${active}" data-nav="${selection}" title="${escapeHtml(`VMID ${container.vmid}`)}">
                    <span class="nav-copy">
                      <span class="nav-item-title">${escapeHtml(sidebarTitleFor(snapshot, container))}</span>
                      ${
                        detail
                          ? `<span class="nav-item-detail">${escapeHtml(detail)}</span>`
                          : ""
                      }
                    </span>
                  </button>
                `;
              })
              .join("");

            const expanded = state.expandedGroups.has(group.id);
            return `
              <div class="nav-group">
                <button class="${headingClass}" data-group-toggle="${escapeHtml(group.id)}" aria-expanded="${expanded ? "true" : "false"}">
                  <span class="nav-group-label">
                    <span class="nav-group-chevron${expanded ? " expanded" : ""}">${icon("chevron")}</span>
                    <span>${escapeHtml(group.title)}</span>
                  </span>
                  <span class="nav-count">${group.containers.length}</span>
                </button>
                ${expanded ? `<div class="nav-list">${items}</div>` : ""}
              </div>
            `;
          })
          .join("")
      : `<div class="sidebar-footer-copy">No containers found yet.</div>`;

  const warningCount = snapshot.warnings.length > 0 ? `<span class="nav-count">${snapshot.warnings.length}</span>` : "";

  return `
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><img class="brand-icon" src="assets/proxnix-icon.png" alt="" /></div>
        <div class="brand-copy">
          <div class="eyebrow">Workstation</div>
          <div class="brand-title">Proxnix Manager</div>
        </div>
      </div>

      <section class="nav-section">
        <div class="nav-heading">
          <span>Actions</span>
          ${warningCount}
        </div>
        <div class="nav-list">
          ${renderNavItem("home", "Welcome", "welcome", state.selection, "")}
          ${renderNavItem("branch", "Git", "git", state.selection, "")}
          ${renderNavItem("publish", "Publish", "publish", state.selection, "")}
          ${renderNavItem("lock", "Secrets", "secrets", state.selection, "")}
          ${renderNavItem("edit", "Site", "site", state.selection, "")}
          ${renderNavItem("health", "Doctor", "doctor", state.selection, "")}
        </div>
      </section>

      <section class="nav-section nav-section-containers">
        <div class="nav-heading">
          <span>Containers</span>
        </div>
        <div class="sidebar-bundle-create">
          <input
            type="text"
            data-option="containerBundleDraftVmid"
            value="${escapeHtml(state.containerBundleDraftVmid)}"
            placeholder="New VMID"
            spellcheck="false"
            ${canCreateBundle ? "" : "disabled"}
          />
          <button
            class="icon-button"
            data-action="create-container-bundle"
            title="Create empty container bundle"
            aria-label="Create empty container bundle"
            ${canCreateBundle && !state.containerBundleRunning && state.containerBundleDraftVmid.trim() ? "" : "disabled"}
          >
            ${icon("box")}
          </button>
        </div>
        <div class="nav-list nav-list-groups">${containerButtons}</div>
      </section>

      <section class="nav-section nav-section-settings">
        <div class="nav-list">
          ${renderNavItem("gear", "Settings", "settings", state.selection, "")}
        </div>
      </section>

      <div class="sidebar-footer">
        <div class="sidebar-footer-copy" title="${escapeHtml(snapshot.configPath)}">
          Config: <code>${escapeHtml(compactPath(snapshot.configPath))}</code>
        </div>
      </div>
    </aside>
  `;
}


function renderWarnings(snapshot: AppSnapshot): string {
  const firstRun = snapshot.config.siteDir.length === 0 && !state.error;
  if (firstRun) {
    return "";
  }

  if (snapshot.warnings.length === 0 && !state.error) {
    return "";
  }

  const items = [...snapshot.warnings];
  if (state.error) {
    items.unshift(state.error);
  }

  return `
    <section class="warning-band">
      ${items
        .map(
          (warning) => `
            <div class="warning">
              ${icon("health")}
              <div>${escapeHtml(warning)}</div>
            </div>
          `,
        )
        .join("")}
    </section>
  `;
}

function renderToolbar(snapshot: AppSnapshot): string {
  const currentContainer = selectedContainer();
  const dirty = isDirty();
  const secretScope = secretScopeFromSelection();

  const title =
    (currentContainer && state.snapshot ? sidebarTitleFor(state.snapshot, currentContainer) : null) ??
    (state.selection === "welcome"
      ? "Welcome"
      : state.selection === "site"
      ? "Site"
      : state.selection === "settings"
      ? "Settings"
      : state.selection === "publish"
        ? "Publish"
        : state.selection === "secrets"
          ? "Secrets"
          : state.selection === "secrets:groups"
            ? "Secret Groups"
            : state.selection === "secrets:containers"
              ? "Container Secrets"
            : secretScope?.scopeType === "shared"
              ? "Shared Secrets"
              : secretScope?.scopeType === "group"
                ? `Group: ${secretScope.scopeId}`
                : secretScope?.scopeType === "container"
                  ? `Container Secrets: ${secretScope.scopeId}`
          : state.selection === "doctor"
            ? "Doctor"
            : state.selection === "git"
              ? "Git"
              : "Proxnix Manager");

  const subtitleMap: Record<string, string> = {
    welcome: "Start from the main workstation tasks.",
    site: "Site-wide NixOS overrides published as site.nix.",
    settings: "Paths, SSH targets, and secret backend used across all proxnix tools.",
    publish: "Sync config, secrets, and identities to your Proxmox hosts.",
    secrets: "Manage shared and named secret groups.",
    "secrets:groups": "Shared is always available; named groups can be attached to containers.",
    "secrets:containers": "Manage each container's local secret scope and identity.",
    doctor: "Check your site for misconfigurations and missing files.",
    git: "Current branch, uncommitted changes, and recent history.",
  };

  const subtitle = currentContainer
    ? "Config files, secret groups, and identity for this container."
    : secretScope
      ? "Manage secret names and write values through proxnix-secrets without revealing stored values."
    : subtitleMap[state.selection] ?? "";

  const canOpenSite = snapshot.config.siteDir.length > 0;

  return `
    <header class="toolbar">
      <div class="toolbar-copy">
        <div class="eyebrow">Proxnix Manager</div>
        <div class="toolbar-title">${escapeHtml(title)}</div>
        <div class="toolbar-subtitle">${escapeHtml(subtitle)}</div>
      </div>
      <div class="toolbar-actions">
        <div class="toolbar-status${state.loading ? " loading" : ""}">
          ${icon(state.loading || state.saving ? "refresh" : "spark")}
          <span>${state.saving ? "Saving config" : state.loading ? "Refreshing state" : dirty ? "Draft changed" : "Synced"}</span>
        </div>
        <button class="icon-button" data-action="refresh" title="Refresh site state" aria-label="Refresh site state">
          ${icon("refresh")}
        </button>
        <button
          class="icon-button"
          data-action="open-site"
          title="Open site directory"
          aria-label="Open site directory"
          ${canOpenSite ? "" : "disabled"}
        >
          ${icon("folder")}
        </button>
      </div>
    </header>
  `;
}

function pill(label: string, tone: "good" | "warn" | "info" | "magenta", iconName?: IconName): string {
  return `<span class="pill ${tone}">${iconName ? icon(iconName) : ""}<span>${escapeHtml(label)}</span></span>`;
}

function renderOpenDropdown(path: string, extraItems?: { label: string; action: string; path: string }[]): string {
  const items = [
    { label: "Directory", action: "open-path", path },
    { label: "Editor", action: "open-in-editor", path },
    ...(extraItems ?? []),
  ];
  return `
    <div class="dropdown">
      <button class="secondary-button dropdown-toggle" data-dropdown-toggle>
        ${icon("open")}
        <span>Open</span>
        <span class="dropdown-caret">${icon("chevron")}</span>
      </button>
      <div class="dropdown-menu">
        ${items.map((item) => `
          <button class="dropdown-item" data-action="${item.action}" data-path="${escapeHtml(item.path)}">
            <span>${escapeHtml(item.label)}</span>
          </button>
        `).join("")}
      </div>
    </div>
  `;
}

function renderSiteNixOpenDropdown(sitePath: string, siteDir: string): string {
  const items = [
    { label: "File", action: "open-path", path: sitePath },
    { label: "Editor", action: "open-in-editor", path: sitePath },
    { label: "Preview", action: "preview-file", path: sitePath },
    { label: "Site Directory", action: "open-path", path: siteDir },
  ];
  return `
    <div class="dropdown">
      <button class="secondary-button dropdown-toggle" data-dropdown-toggle>
        ${icon("open")}
        <span>Open</span>
        <span class="dropdown-caret">${icon("chevron")}</span>
      </button>
      <div class="dropdown-menu">
        ${items.map((item) => `
          <button class="dropdown-item" data-action="${item.action}" data-path="${escapeHtml(item.path)}" data-preview-title="site.nix">
            <span>${escapeHtml(item.label)}</span>
          </button>
        `).join("")}
      </div>
    </div>
  `;
}

function renderOnboarding(snapshot: AppSnapshot): string {
  const draft = onboardingConfig();
  const running = state.onboardingRunning;
  const canStart = canRunOnboarding(draft);
  const backendNeeds = usesEmbeddedSops(draft.secretProvider)
    ? renderSettingsField(
        "Master identity",
        "sopsMasterIdentity",
        draft.sopsMasterIdentity,
        "Created if missing; used as the embedded-sops recovery key.",
        undefined,
        true,
      )
    : usesExecProvider(draft.secretProvider)
      ? renderSettingsField(
          "Provider command",
          "secretProviderCommand",
          draft.secretProviderCommand,
          "Command implementing the proxnix secret-provider protocol.",
          undefined,
          true,
        )
      : `
        <div class="onboarding-note wide">
          ${icon("key")}
          <span>The master and host-relay keys will be stored in the selected provider under proxnix internal keys.</span>
        </div>
      `;

  return `
    <div class="page-stack">
      <section class="onboarding-flow">
        <div class="hero-copy">
          <div class="eyebrow">Getting started</div>
          <div class="hero-title">Create a proxnix site repo</div>
          <div class="hero-text">
            Choose where the live site state should live, select the secret backend,
            then scaffold the repo and create the master and host-relay keys.
          </div>
        </div>

        ${state.onboardingResult ? `<div class="${state.onboardingResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.onboardingResult.output || state.onboardingResult.error || "")}</div>` : ""}

        <div class="onboarding-steps">
          <div class="onboarding-step">
            <div class="onboarding-step-index">1</div>
            <div class="onboarding-step-body">
              <div class="section-title">${icon("folder")}<span>Site Path</span></div>
              <div class="form-grid compact-form-grid">
                ${renderSettingsField("Repository path", "siteDir", draft.siteDir, "The directory will be created if it does not exist.", undefined, true, true)}
              </div>
            </div>
          </div>

          <div class="onboarding-step">
            <div class="onboarding-step-index">2</div>
            <div class="onboarding-step-body">
              <div class="section-title">${icon("lock")}<span>Secrets Backend</span></div>
              <div class="form-grid compact-form-grid">
                ${renderSettingsField("Backend", "secretProvider", draft.secretProvider, "Where proxnix stores source secrets and generated keys.", SECRET_PROVIDER_OPTIONS, true)}
                ${backendNeeds}
              </div>
            </div>
          </div>

          <div class="onboarding-step">
            <div class="onboarding-step-index">3</div>
            <div class="onboarding-step-body">
              <div class="section-title">${icon("publish")}<span>Scaffold</span></div>
              <div class="section-copy">
                Creates site.nix, containers and private directories, initializes git, saves the workstation config, and creates master plus host-relay keys.
              </div>
            </div>
          </div>
        </div>

        <div class="hero-actions">
          <button class="primary-button" data-action="run-onboarding" ${canStart ? "" : "disabled"}>
            ${icon(running ? "refresh" : "spark")}
            <span>${running ? "Creating..." : "Create Site"}</span>
          </button>
          <button class="secondary-button" data-nav="settings">
            ${icon("gear")}
            <span>Advanced Settings</span>
          </button>
        </div>
      </section>
      ${snapshot.configExists ? renderSettingsForm(snapshot) : ""}
    </div>
  `;
}

function renderWelcomePage(snapshot: AppSnapshot): string {
  const hasSite = snapshot.config.siteDir.length > 0 && snapshot.siteDirExists;
  const containerCount = snapshot.containers.length;
  const groupCount = new Set(["shared", ...snapshot.definedSecretGroups, ...snapshot.attachedSecretGroups]).size;

  return `
    <div class="page-stack">
      <section class="welcome-band">
        <div class="welcome-copy">
          <div class="welcome-text">
            ${hasSite
              ? `Using ${snapshot.config.siteDir}.`
              : "Choose a proxnix site directory to begin."}
          </div>
        </div>
        <div class="welcome-actions">
          ${hasSite
            ? `
              <button class="primary-button" data-nav="secrets">
                ${icon("lock")}
                <span>Manage Secrets</span>
              </button>
              <button class="secondary-button" data-nav="site">
                ${icon("edit")}
                <span>Review site.nix</span>
              </button>
              <button class="secondary-button" data-nav="publish">
                ${icon("publish")}
                <span>Publish</span>
              </button>
            `
            : `
              <button class="primary-button" data-action="choose-site">
                ${icon("folder")}
                <span>Choose Site Directory</span>
              </button>
              <button class="secondary-button" data-nav="settings">
                ${icon("gear")}
                <span>Settings</span>
              </button>
            `}
        </div>
      </section>

      <section class="page-band">
        <div class="welcome-stat-grid">
          <button class="welcome-stat" data-nav="secrets:containers" ${hasSite ? "" : "disabled"}>
            <span class="welcome-stat-value">${containerCount}</span>
            <span class="welcome-stat-label">Containers</span>
          </button>
          <button class="welcome-stat" data-nav="secrets" ${hasSite ? "" : "disabled"}>
            <span class="welcome-stat-value">${groupCount}</span>
            <span class="welcome-stat-label">Secret groups</span>
          </button>
        </div>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("spark")}<span>Start</span></div>
            <div class="section-copy">Common workstation tasks for this site.</div>
          </div>
        </div>
        <div class="welcome-link-list">
          <button class="welcome-link" data-nav="secrets" ${hasSite ? "" : "disabled"}>
            <span>${icon("lock")}</span>
            <span>Manage shared, group, and container secrets</span>
          </button>
          <button class="welcome-link" data-nav="site" ${hasSite ? "" : "disabled"}>
            <span>${icon("edit")}</span>
            <span>Review site.nix</span>
          </button>
          <button class="welcome-link" data-nav="doctor" ${hasSite ? "" : "disabled"}>
            <span>${icon("health")}</span>
            <span>Run site checks</span>
          </button>
          <button class="welcome-link" data-nav="git" ${hasSite ? "" : "disabled"}>
            <span>${icon("branch")}</span>
            <span>Review repository changes</span>
          </button>
          <button class="welcome-link" data-nav="settings">
            <span>${icon("gear")}</span>
            <span>Adjust workstation settings</span>
          </button>
        </div>
      </section>
    </div>
  `;
}

function renderSettingsField(
  label: string,
  field: string,
  value: string,
  hint: string,
  options?: string[],
  wide = false,
  browse = false,
  attributeName = "data-field",
): string {
  const control = options
    ? `
        <div class="field-control">
          <select ${attributeName}="${field}">
            ${options
              .map(
                (option) =>
                  `<option value="${escapeHtml(option)}"${option === value ? " selected" : ""}>${escapeHtml(option)}</option>`,
              )
              .join("")}
          </select>
        </div>
      `
    : browse
      ? `
          <div class="field-control-with-button">
            <input ${attributeName}="${field}" value="${escapeHtml(value)}" spellcheck="false" />
            <button class="secondary-button" data-action="choose-site" title="Choose site directory">
              ${icon("folder")}
              <span>Browse</span>
            </button>
          </div>
        `
      : `
          <div class="field-control">
            <input ${attributeName}="${field}" value="${escapeHtml(value)}" spellcheck="false" />
          </div>
        `;

  return `
    <label class="field${wide ? " wide" : ""}">
      <div class="field-label-row">
        <span class="field-label">${escapeHtml(label)}</span>
      </div>
      ${control}
      <div class="field-hint">${escapeHtml(hint)}</div>
    </label>
  `;
}

function renderSettingsForm(snapshot: AppSnapshot): string {
  const draft = state.draft ?? defaultConfig();
  const dirty = isDirty();
  const preserved =
    snapshot.preservedConfigKeys.length > 0
      ? `${snapshot.preservedConfigKeys.length} extra config key${snapshot.preservedConfigKeys.length === 1 ? "" : "s"} preserved`
      : "Unknown config keys preserved on save";

  return `
    <section class="page-band">
      <div class="section-header">
        <div>
          <div class="section-title">${icon("gear")}<span>Settings</span></div>
          <div class="section-copy">
            Changes are saved to the shared proxnix config and take effect immediately.
          </div>
        </div>
        <div class="section-actions">
          <button class="ghost-button" data-action="open-config">
            ${icon("open")}
            <span>Open Config</span>
          </button>
          <button class="secondary-button" data-action="reset-draft" ${dirty ? "" : "disabled"}>
            ${icon("refresh")}
            <span>Reset</span>
          </button>
          <button class="primary-button" data-action="save-config" ${dirty ? "" : "disabled"}>
            ${icon("publish")}
            <span>Save</span>
          </button>
        </div>
      </div>

      <div class="pill-row">
        ${pill(snapshot.configExists ? "Config found" : "New config", snapshot.configExists ? "good" : "warn", snapshot.configExists ? "spark" : "gear")}
        ${pill(dirty ? "Unsaved changes" : "Saved", dirty ? "warn" : "info", dirty ? "refresh" : "spark")}
        ${pill(preserved, "magenta", "lock")}
      </div>

      <div class="form-grid">
        ${renderSettingsField("Site directory", "siteDir", draft.siteDir, "Root of your proxnix site repo.", undefined, true, true)}
        ${renderSettingsField("SSH hosts", "hosts", draft.hosts, "Publish targets, e.g. root@node1 root@node2.")}
        ${renderSettingsField("SSH identity", "sshIdentity", draft.sshIdentity, "Key for ssh -i. Blank uses your SSH agent.")}
        ${renderSettingsField("Remote dir", "remoteDir", draft.remoteDir, "Public proxnix path on target hosts.")}
        ${renderSettingsField("Remote private dir", "remotePrivDir", draft.remotePrivDir, "Private proxnix path on target hosts.")}
        ${renderSettingsField("Host relay identity", "remoteHostRelayIdentity", draft.remoteHostRelayIdentity, "Age identity path on target hosts.")}
        ${renderSettingsField("Secret backend", "secretProvider", draft.secretProvider, "How secrets and identities are stored.", SECRET_PROVIDER_OPTIONS)}
        ${usesEmbeddedSops(draft.secretProvider)
          ? renderSettingsField(
              "SOPS master identity",
              "sopsMasterIdentity",
              draft.sopsMasterIdentity,
              "SSH private key for the embedded-sops master identity.",
              undefined,
              true,
            )
          : ""}
        ${usesExecProvider(draft.secretProvider)
          ? renderSettingsField(
              "Provider command",
              "secretProviderCommand",
              draft.secretProviderCommand,
              "Command to run for the exec backend.",
              undefined,
              true,
            )
          : ""}
        ${renderSettingsField("Scripts dir", "scriptsDir", draft.scriptsDir, "Override path for proxnix command wrappers.", undefined, true)}
        ${renderSettingsField("Manager Python path", "managerPythonPath", draft.managerPythonPath, "Advanced: extra import paths for the Manager bridge. Site-packages paths are preferred; venv bin paths are expanded automatically.", undefined, true)}
      </div>
    </section>
  `;
}

function renderSitePage(snapshot: AppSnapshot): string {
  const canCreate = snapshot.siteDirExists && !snapshot.siteNixExists;
  const sitePath = snapshot.siteNixPath || `${snapshot.config.siteDir}/site.nix`;

  return `
    <div class="page-stack">
      ${state.siteNixResult ? `<div class="${state.siteNixResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.siteNixResult.output || state.siteNixResult.error || "")}</div>` : ""}

      <section class="page-controls">
        <div class="controls-start">
          <div class="status-line">
            <span class="${snapshot.siteNixExists ? "status-dot good" : "status-dot warn"}"></span>
            <span>${snapshot.siteNixExists ? "site.nix found" : "site.nix missing"}</span>
            <code>${escapeHtml(compactPath(sitePath))}</code>
          </div>
        </div>
        <div class="controls-end">
          ${
            snapshot.siteNixExists
              ? renderSiteNixOpenDropdown(sitePath, snapshot.config.siteDir)
              : `<button class="secondary-button" data-action="open-path" data-path="${escapeHtml(snapshot.config.siteDir)}">${icon("folder")}<span>Open Directory</span></button>`
          }
        </div>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("edit")}<span>site.nix</span></div>
          </div>
          <div class="section-actions">
            ${
              snapshot.siteNixExists
                ? `<button class="secondary-button" data-action="open-in-editor" data-path="${escapeHtml(sitePath)}">${icon("edit")}<span>Edit</span></button>`
                : `<button class="primary-button" data-action="create-site-nix" ${canCreate ? "" : "disabled"}>${icon("publish")}<span>Create site.nix</span></button>`
            }
          </div>
        </div>

        <pre class="embedded-file-preview">${escapeHtml(snapshot.siteNixExists ? snapshot.siteNixContent : "site.nix has not been created yet.")}</pre>
      </section>
    </div>
  `;
}

function syncDraftIndicators(): void {
  const dirty = isDirty();
  const saveButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="save-config"]');
  const resetButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="reset-draft"]');
  const onboardingButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="run-onboarding"]');
  const status = root.querySelector<HTMLDivElement>(".toolbar-status");
  const statusLabel = status?.querySelector("span") ?? null;

  for (const button of saveButtons) {
    button.disabled = !dirty || state.saving;
  }

  for (const button of resetButtons) {
    button.disabled = !dirty || state.saving;
  }

  for (const button of onboardingButtons) {
    button.disabled = !canRunOnboarding(onboardingConfig());
  }

  if (status && statusLabel && !state.loading && !state.saving) {
    status.classList.toggle("loading", false);
    statusLabel.textContent = dirty ? "Draft changed" : "Synced";
  }
}

function sidebarMetadataDirty(container: ContainerSummary, snapshot: AppSnapshot): boolean {
  const current = currentContainerSidebarMetadata(snapshot, container.vmid);
  const draft = state.containerMetadataDraft ?? defaultSidebarMetadata();
  return JSON.stringify(current) !== JSON.stringify(draft);
}

function syncSidebarMetadataIndicators(): void {
  const container = selectedContainer();
  const snapshot = state.snapshot;
  const saveButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="save-sidebar-metadata"]');
  const resetButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="reset-sidebar-metadata"]');
  const clearButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="clear-sidebar-metadata"]');
  const dirty = container && snapshot ? sidebarMetadataDirty(container, snapshot) : false;
  const hasPendingPickerInput = state.sidebarGroupInput.trim().length > 0 || state.sidebarLabelInput.trim().length > 0;
  const hasMetadata =
    (state.containerMetadataDraft?.displayName ?? "").length > 0 ||
    (state.containerMetadataDraft?.group ?? "").length > 0 ||
    (state.containerMetadataDraft?.labels.length ?? 0) > 0;

  for (const button of saveButtons) {
    button.disabled = (!dirty && !hasPendingPickerInput) || state.metadataSaving;
  }
  for (const button of resetButtons) {
    button.disabled = (!dirty && !hasPendingPickerInput) || state.metadataSaving;
  }
  for (const button of clearButtons) {
    button.disabled = (!hasMetadata && !hasPendingPickerInput) || state.metadataSaving;
  }
}

function syncSecretDraftIndicators(): void {
  const setButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="set-secret"]');
  const canSet =
    !state.secretScopeRunning &&
    state.secretDraftName.trim().length > 0 &&
    state.secretDraftValue.length > 0;
  for (const button of setButtons) {
    button.disabled = !canSet;
  }
}

function syncSecretTopologyIndicators(): void {
  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="create-secret-group"]')) {
    button.disabled = state.secretTopologyRunning || state.secretGroupDraftName.trim().length === 0;
  }
  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="attach-secret-group"]')) {
    button.disabled = state.secretTopologyRunning || state.containerSecretGroupDraft.trim().length === 0;
  }
}

function syncContainerBundleIndicators(): void {
  for (const button of root.querySelectorAll<HTMLButtonElement>('[data-action="create-container-bundle"]')) {
    button.disabled = state.containerBundleRunning || state.containerBundleDraftVmid.trim().length === 0;
  }
}

function renderSidebarMetadataForm(container: ContainerSummary, snapshot: AppSnapshot): string {
  const metadata = state.containerMetadataDraft ?? currentContainerSidebarMetadata(snapshot, container.vmid);
  const dirty = sidebarMetadataDirty(container, snapshot);
  const hasMetadata = metadata.displayName || metadata.group || metadata.labels.length > 0;
  const isOpen = state.displaySettingsOpen;
  const summary = [
    metadata.displayName ? `Alias: ${metadata.displayName}` : "",
    metadata.group ? `Group: ${metadata.group}` : "",
    metadata.labels.length > 0 ? `${metadata.labels.length} label(s)` : "",
  ]
    .filter(Boolean)
    .join(" / ");

  return `
    <section class="display-settings-disclosure${isOpen ? " open" : ""}">
      <button
        class="display-settings-toggle"
        type="button"
        data-action="toggle-display-settings"
        aria-expanded="${isOpen ? "true" : "false"}"
      >
        <span class="display-settings-summary-title">${icon("spark")}<span>Display Settings</span></span>
        <span class="display-settings-summary-copy">${escapeHtml(summary || "Sidebar presentation only")}</span>
        <span class="display-settings-toggle-label">${isOpen ? "Hide" : "Edit"}</span>
      </button>

      ${
        isOpen
          ? `<div class="display-settings-body">
              <div class="section-header">
                <div class="section-copy">
                  Custom name, group, and labels for this container in the sidebar.
                </div>
                <div class="section-actions">
                  <button class="secondary-button" data-action="clear-sidebar-metadata" ${hasMetadata ? "" : "disabled"}>
                    ${icon("refresh")}
                    <span>Clear</span>
                  </button>
                  <button class="secondary-button" data-action="reset-sidebar-metadata" ${dirty ? "" : "disabled"}>
                    ${icon("refresh")}
                    <span>Reset</span>
                  </button>
                  <button class="primary-button" data-action="save-sidebar-metadata" ${dirty ? "" : "disabled"}>
                    ${icon("publish")}
                    <span>Save</span>
                  </button>
                </div>
              </div>

              <div class="form-grid">
                ${renderSettingsField("Display name", "displayName", metadata.displayName, "Friendly name shown instead of the VMID.", undefined, true, false, "data-container-field")}
                ${renderOrganizationalGroupPicker(container, snapshot, metadata)}
                ${renderLabelPicker(container, snapshot, metadata)}
              </div>
            </div>`
          : ""
      }
    </section>
  `;
}

function organizationalGroupOptions(
  container: ContainerSummary,
  snapshot: AppSnapshot,
  metadata: SidebarMetadata,
): string[] {
  const groups = new Map<string, string>();
  for (const [vmid, candidateMetadata] of Object.entries(snapshot.sidebarMetadata)) {
    const group = normalizeString(candidateMetadata?.group).trim();
    if (!group || vmid === container.vmid) {
      continue;
    }
    groups.set(group.toLocaleLowerCase(), group);
  }
  if (metadata.group) {
    groups.set(metadata.group.toLocaleLowerCase(), metadata.group);
  }
  return [...groups.values()].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
}

function sidebarLabelOptions(
  container: ContainerSummary,
  snapshot: AppSnapshot,
  metadata: SidebarMetadata,
): string[] {
  const labels = new Map<string, string>();
  for (const [vmid, candidateMetadata] of Object.entries(snapshot.sidebarMetadata)) {
    if (vmid === container.vmid) {
      continue;
    }
    for (const label of normalizeStringList(candidateMetadata?.labels)) {
      labels.set(label.toLocaleLowerCase(), label);
    }
  }
  for (const label of metadata.labels) {
    labels.set(label.toLocaleLowerCase(), label);
  }
  return [...labels.values()].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
}

function renderOrganizationalGroupPicker(
  container: ContainerSummary,
  snapshot: AppSnapshot,
  metadata: SidebarMetadata,
): string {
  const options = organizationalGroupOptions(container, snapshot, metadata);
  const selected = metadata.group.trim();
  const suggestions = options.filter((group) => group !== selected);
  const optionButtons =
    suggestions.length > 0
      ? suggestions
          .map(
            (group) => `
              <button class="metadata-picker-option" type="button" data-action="set-sidebar-group" data-group-value="${escapeHtml(group)}">
                ${escapeHtml(group)}
              </button>
            `,
          )
          .join("")
      : `<span class="metadata-picker-empty">No saved groups yet</span>`;

  return `
    <div class="field organizational-group-picker">
      <div class="field-label-row">
        <label class="field-label" for="sidebar-group-input">Group</label>
        <span class="field-hint">Sidebar group heading.</span>
      </div>
      <div class="metadata-picker">
        <div class="metadata-picker-input-row">
          ${
            selected
              ? `<button class="metadata-picker-token" type="button" data-action="set-sidebar-group" data-group-value="">
                  <span>${escapeHtml(selected)}</span>
                  <span aria-hidden="true">Clear</span>
                </button>`
              : ""
          }
          <input
            id="sidebar-group-input"
            type="text"
            data-sidebar-group-input="true"
            value="${escapeHtml(state.sidebarGroupInput)}"
            placeholder="${selected ? "Change group" : "Type group and press Enter"}"
            spellcheck="false"
          />
        </div>
        <div class="metadata-picker-suggestions">
          <span>Suggestions</span>
          <div>${optionButtons}</div>
        </div>
      </div>
    </div>
  `;
}

function renderLabelPicker(
  container: ContainerSummary,
  snapshot: AppSnapshot,
  metadata: SidebarMetadata,
): string {
  const selectedKeys = new Set(metadata.labels.map((label) => label.toLocaleLowerCase()));
  const suggestions = sidebarLabelOptions(container, snapshot, metadata).filter(
    (label) => !selectedKeys.has(label.toLocaleLowerCase()),
  );
  const tokens =
    metadata.labels.length > 0
      ? metadata.labels
          .map(
            (label) => `
              <button class="metadata-picker-token" type="button" data-action="remove-sidebar-label" data-label-value="${escapeHtml(label)}">
                <span>${escapeHtml(label)}</span>
                <span aria-hidden="true">Remove</span>
              </button>
            `,
          )
          .join("")
      : "";
  const optionButtons =
    suggestions.length > 0
      ? suggestions
          .map(
            (label) => `
              <button class="metadata-picker-option" type="button" data-action="add-sidebar-label" data-label-value="${escapeHtml(label)}">
                ${escapeHtml(label)}
              </button>
            `,
          )
          .join("")
      : `<span class="metadata-picker-empty">No saved labels yet</span>`;

  return `
    <div class="field wide">
      <div class="field-label-row">
        <label class="field-label" for="sidebar-label-input">Labels</label>
        <span class="field-hint">Sidebar tags.</span>
      </div>
      <div class="metadata-picker">
        <div class="metadata-picker-input-row">
          ${tokens}
          <input
            id="sidebar-label-input"
            type="text"
            data-sidebar-label-input="true"
            value="${escapeHtml(state.sidebarLabelInput)}"
            placeholder="Type labels and press Enter"
            spellcheck="false"
          />
        </div>
        <div class="metadata-picker-suggestions">
          <span>Suggestions</span>
          <div>${optionButtons}</div>
        </div>
      </div>
    </div>
  `;
}

function renderContainerPage(container: ContainerSummary): string {
  const snapshot = state.snapshot;
  const isEmbeddedSops = usesEmbeddedSops(snapshot?.config.secretProvider ?? "");
  const confirmingDelete = state.pendingDeleteContainerBundleVmid === container.vmid;

  return `
    <div class="page-stack">
      ${state.containerBundleResult ? `<div class="${state.containerBundleResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.containerBundleResult.output || state.containerBundleResult.error || "")}</div>` : ""}
      <section class="container-config-row">
        ${snapshot ? renderSidebarMetadataForm(container, snapshot) : ""}
        <div class="container-config-actions">
          ${renderOpenDropdown(
            container.containerPath,
            isEmbeddedSops
              ? [{ label: "Private Directory", action: "open-path", path: container.privateContainerPath }]
              : undefined,
          )}
        </div>
      </section>

      <section class="page-band">
        <div class="details-grid">
          <div class="list-block">
            <div class="section-title">${icon("box")}<span>Drop-ins</span></div>
            <div class="list">
              ${
                container.dropins.length > 0
                  ? container.dropins
                      .map(
                        (dropin) => {
                          const path = `${container.containerPath}/dropins/${dropin}`;
                          return `
                          <button class="list-item preview-list-item" data-action="preview-file" data-preview-title="${escapeHtml(dropin)}" data-path="${escapeHtml(path)}">
                            <div class="list-item-copy">
                              <div class="list-item-title"><code>${escapeHtml(dropin)}</code></div>
                              <div class="list-item-meta">repo overlay / preview</div>
                            </div>
                            ${icon("open")}
                          </button>
                        `;
                        },
                      )
                      .join("")
                  : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No drop-ins configured.</div></div></div>`
              }
            </div>
          </div>

          <div class="list-block">
            ${renderContainerSecrets(container)}
          </div>
        </div>
      </section>

      <section class="container-danger-row">
        <div>
          <div class="container-danger-title">Container Bundle</div>
          <div class="container-danger-copy">Deletes the scaffold and provider-backed identity. Container-local secrets must be removed first.</div>
        </div>
        <button class="secondary-button${confirmingDelete ? " danger-button" : ""}" data-action="delete-container-bundle" data-vmid="${escapeHtml(container.vmid)}" ${state.containerBundleRunning ? "disabled" : ""}>
          ${icon("trash")}
          <span>${confirmingDelete ? "Sure?" : "Delete Bundle"}</span>
        </button>
      </section>
    </div>
  `;
}

function renderContainerSecrets(container: ContainerSummary): string {
  const snapshot = state.snapshot;
  const status = state.secretScopeStatus?.scopeType === "container" && state.secretScopeStatus.scopeId === container.vmid
    ? state.secretScopeStatus
    : null;
  const secretWarnings = [
    ...(state.secretScopeError ? [state.secretScopeError] : []),
    ...(status?.warnings ?? []),
  ];
  const secretRows = state.secretScopeLoading
    ? `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">Loading secrets...</div></div></div>`
    : status && status.entries.length > 0
      ? status.entries
          .map(
            (entry) => `
              <div class="list-item">
                <div class="list-item-copy">
                  <div class="list-item-title"><code>${escapeHtml(entry.name)}</code></div>
                  <div class="list-item-meta">source: ${escapeHtml(entry.source)}</div>
                </div>
              </div>
            `,
          )
          .join("")
      : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No secrets configured.</div></div></div>`;
  const attachableGroups = snapshot
    ? allNamedSecretGroups(snapshot).filter((group) => !container.secretGroups.includes(group))
    : [];
  const attachOptions = attachableGroups
    .map((group) => `<option value="${escapeHtml(group)}"></option>`)
    .join("");

  return `
    <div class="section-title">${icon("lock")}<span>Secrets</span></div>
    ${secretWarnings.length > 0 ? `<div class="error-band compact-band">${secretWarnings.map((warning) => escapeHtml(warning)).join("<br />")}</div>` : ""}
    ${state.secretTopologyResult ? `<div class="${state.secretTopologyResult.exitCode === 0 ? "success-band compact-band" : "error-band compact-band"}">${escapeHtml(state.secretTopologyResult.output || state.secretTopologyResult.error || "")}</div>` : ""}
    <div class="nested-list-block">
      <div class="list-subtitle">Groups</div>
      <div class="inline-action-row">
        <input
          class="inline-action-input"
          type="text"
          list="container-secret-groups-${escapeHtml(container.vmid)}"
          data-option="containerSecretGroupDraft"
          value="${escapeHtml(state.containerSecretGroupDraft)}"
          placeholder="Attach group"
          spellcheck="false"
        />
        <datalist id="container-secret-groups-${escapeHtml(container.vmid)}">${attachOptions}</datalist>
        <button class="secondary-button compact-button" data-action="attach-secret-group" data-vmid="${escapeHtml(container.vmid)}" ${state.secretTopologyRunning || !state.containerSecretGroupDraft.trim() ? "disabled" : ""}>Attach</button>
      </div>
      <div class="list">
        ${
          container.secretGroups.length > 0
            ? container.secretGroups
                .map(
                  (group) => `
                    <div class="list-item">
                      <div class="list-item-copy">
                        <div class="list-item-title"><code>${escapeHtml(group)}</code></div>
                        <div class="list-item-meta">attached to this container</div>
                      </div>
                      <button class="secondary-button compact-button" data-action="detach-secret-group" data-vmid="${escapeHtml(container.vmid)}" data-secret-group="${escapeHtml(group)}" ${state.secretTopologyRunning ? "disabled" : ""}>Detach</button>
                    </div>
                  `,
                )
                .join("")
            : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No groups attached.</div></div></div>`
        }
      </div>
    </div>
    <div class="nested-list-block">
      <div class="list-subtitle">Secrets</div>
      <div class="list">${secretRows}</div>
    </div>
  `;
}

function containerStatusSummary(container: ContainerSummary): string {
  const status = container.hasConfig ? "Config found" : "No config dir";
  const secrets = `${container.secretGroups.length} secret group${container.secretGroups.length === 1 ? "" : "s"}`;
  const identity = effectiveContainerHasIdentity(container) ? "Identity present" : "No identity";
  return `${status} / ${secrets} / ${identity}`;
}

function selectedContainerStatusbarLabel(): string | null {
  if (!state.selection.startsWith("container:")) {
    return null;
  }
  const container = selectedContainer();
  if (!container) {
    return null;
  }
  return `Container config: <code title="${escapeHtml(container.containerPath)}">${escapeHtml(compactPath(container.containerPath))}</code> <span>${escapeHtml(containerStatusSummary(container))}</span>`;
}

function fileStatusClass(status: string): string {
  if (status.includes("M")) return "modified";
  if (status.includes("A")) return "added";
  if (status.includes("D")) return "deleted";
  if (status.includes("R")) return "renamed";
  if (status.includes("?")) return "untracked";
  return "modified";
}

function gitFilesFor(result: GitStatusResult, key: "staged" | "unstaged" | "untracked"): GitFile[] {
  const explicit = result[key];
  if (explicit) {
    return explicit;
  }

  if (key === "untracked") {
    return result.files.filter((file) => file.status.includes("?"));
  }
  if (key === "staged") {
    return result.files.filter((file) => !file.status.includes("?") && file.status.length === 1);
  }
  return result.files.filter((file) => !file.status.includes("?") && file.status.length > 1);
}

function renderDoctorPage(snapshot: AppSnapshot): string {
  const result = state.doctorResult;
  const running = state.doctorRunning;

  const resultsHtml = running
    ? `<div class="running-band">${icon("refresh")}<span>Running health check...</span></div>`
    : result
      ? (() => {
          const errorBand = result.error
            ? `<div class="error-band">${escapeHtml(result.error)}</div>`
            : "";

          const summaryPills =
            result.sections.length > 0
              ? `<div class="pill-row">
                  ${pill(`${result.oks} passed`, "good", "spark")}
                  ${pill(`${result.warns} warning${result.warns === 1 ? "" : "s"}`, result.warns > 0 ? "warn" : "info", "health")}
                  ${pill(`${result.fails} failure${result.fails === 1 ? "" : "s"}`, result.fails > 0 ? "warn" : "info", "health")}
                </div>`
              : "";

          const sections = result.sections
            .map(
              (section) => `
                <div class="doctor-section">
                  <div class="doctor-section-heading">${escapeHtml(section.heading)}</div>
                  <div class="doctor-entries">
                    ${section.entries
                      .map(
                        (entry) => `
                          <div class="doctor-entry ${escapeHtml(entry.level)}">
                            <span class="entry-level">${escapeHtml(entry.level)}</span>
                            <span class="entry-text">${escapeHtml(entry.text)}</span>
                          </div>
                        `,
                      )
                      .join("")}
                  </div>
                </div>
              `,
            )
            .join("");

          return `${errorBand}${summaryPills}<div class="doctor-results">${sections}</div>`;
        })()
      : `<div class="empty-state">Run a health check to look for problems in your site.</div>`;

  return `
    <div class="page-stack">
      <section class="page-controls">
        <div class="controls-start">
          <div class="option-field">
            <span class="option-field-label">Target VMID</span>
            <input type="text" data-option="doctorVmid" value="${escapeHtml(state.doctorVmid)}" placeholder="All" spellcheck="false" />
          </div>
          <label class="option-toggle">
            <input type="checkbox" data-option="doctorConfigOnly" ${state.doctorConfigOnly ? "checked" : ""} />
            <span>Config only</span>
          </label>
        </div>
        <div class="controls-end">
          <button class="primary-button" data-action="run-doctor" ${running ? "disabled" : ""}>
            ${icon("health")}
            <span>${running ? "Running..." : "Run Health Check"}</span>
          </button>
        </div>
      </section>

      <section class="page-band">
        ${resultsHtml}
      </section>
    </div>
  `;
}

function renderPublishPage(snapshot: AppSnapshot): string {
  const result = state.publishResult;
  const running = state.publishRunning;
  const hosts = snapshot.config.hosts
    .split(/[,\s]+/)
    .map((host) => host.trim())
    .filter(Boolean);
  const hasHosts = hosts.length > 0;

  const resultsHtml = running
    ? `<div class="running-band">${icon("refresh")}<span>Publishing...</span></div>`
    : result
      ? (() => {
          const statusBand =
            result.exitCode === 0
              ? `<div class="success-band">${icon("spark")} Publish completed successfully.</div>`
              : `<div class="error-band">${escapeHtml(result.error || "Publish failed.")}</div>`;
          const output = result.output
            ? `<div class="terminal-output">${escapeHtml(result.output)}</div>`
            : "";
          return `${statusBand}${output}`;
        })()
      : hasHosts
        ? `<div class="empty-state">Preview changes or publish to your hosts.</div>`
        : `<div class="empty-state">Add target hosts in Settings first.</div>`;

  return `
    <div class="page-stack">
      <section class="publish-controls">
        <div class="publish-control-stack">
          <div class="controls-start">
            <div class="option-field">
              <span class="option-field-label">Target VMID</span>
              <input type="text" data-option="publishVmid" value="${escapeHtml(state.publishVmid)}" placeholder="All" spellcheck="false" />
            </div>
            <label class="option-toggle">
              <input type="checkbox" data-option="publishConfigOnly" ${state.publishConfigOnly ? "checked" : ""} />
              <span>Config only</span>
            </label>
          </div>
          <div class="publish-actions">
            <button class="secondary-button" data-action="publish-preview" ${running || !hasHosts ? "disabled" : ""}>
              ${icon("refresh")}
              <span>Preview Changes</span>
            </button>
            <button class="primary-button" data-action="publish-execute" ${running || !hasHosts ? "disabled" : ""}>
              ${icon("publish")}
              <span>${running ? "Publishing..." : "Publish Now"}</span>
            </button>
          </div>
        </div>
        <div class="publish-hosts ${hasHosts ? "" : "empty"}">
          <div class="publish-hosts-header">
            <span class="publish-hosts-label">Hosts</span>
            <span class="publish-hosts-count">${hosts.length}</span>
          </div>
          <div class="host-list" aria-label="Publish hosts">
            ${
              hasHosts
                ? hosts.map((host) => `<span class="host-chip">${escapeHtml(host)}</span>`).join("")
                : `<span class="host-chip">None configured</span>`
            }
          </div>
        </div>
      </section>

      <section class="page-band">
        ${resultsHtml}
      </section>
    </div>
  `;
}

function renderGitPage(snapshot: AppSnapshot): string {
  const result = state.gitResult;
  const loading = state.gitLoading || state.gitRunning;

  if (loading && !result) {
    return `
      <div class="page-stack">
        <section class="page-band">
          <div class="running-band">${icon("refresh")}<span>Loading repository status...</span></div>
        </section>
      </div>
    `;
  }

  if (!result || result.error) {
    return `
      <div class="page-stack">
        <section class="page-band">
          <div class="error-band">${result?.error ? escapeHtml(result.error) : "Could not load repository status."}</div>
        </section>
      </div>
    `;
  }

  const stagedFiles = gitFilesFor(result, "staged");
  const unstagedFiles = gitFilesFor(result, "unstaged");
  const untrackedFiles = gitFilesFor(result, "untracked");
  const unstagedTotal = unstagedFiles.length + untrackedFiles.length;
  const ahead = result.ahead ?? 0;
  const hasRemote = Boolean(result.hasRemote);

  const gitFileRow = (file: GitFile, canAdd: boolean): string => `
    <div class="list-item git-file-row">
      <div class="list-item-copy" style="flex-direction:row;align-items:center;gap:10px;">
        <span class="file-status-code ${fileStatusClass(file.status)}">${escapeHtml(file.status)}</span>
        <code class="list-item-title">${escapeHtml(file.path)}</code>
      </div>
      ${
        canAdd
          ? `<button class="secondary-button compact-button" data-action="git-add-file" data-git-path="${escapeHtml(file.path)}" ${loading ? "disabled" : ""}>Add</button>`
          : ""
      }
    </div>
  `;

  const filesHtml =
    result.files.length > 0
      ? `
          ${
            stagedFiles.length > 0
              ? `<div class="git-file-group"><div class="git-file-heading">Staged</div>${stagedFiles.map((file) => gitFileRow(file, false)).join("")}</div>`
              : ""
          }
          ${
            unstagedFiles.length > 0
              ? `<div class="git-file-group"><div class="git-file-heading">Modified</div>${unstagedFiles.map((file) => gitFileRow(file, true)).join("")}</div>`
              : ""
          }
          ${
            untrackedFiles.length > 0
              ? `<div class="git-file-group"><div class="git-file-heading">Untracked</div>${untrackedFiles.map((file) => gitFileRow(file, true)).join("")}</div>`
              : ""
          }
        `
      : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">Working tree is clean.</div></div></div>`;

  const logHtml =
    result.log.length > 0
      ? result.log
          .map(
            (entry) => `
              <div class="list-item">
                <div class="list-item-copy" style="flex-direction:row;align-items:center;gap:10px;">
                  <span class="commit-hash">${escapeHtml(entry.hash)}</span>
                  <span class="list-item-title">${escapeHtml(entry.message)}</span>
                </div>
              </div>
            `,
          )
          .join("")
      : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No commits found.</div></div></div>`;

  return `
    <div class="page-stack">
      <section class="page-controls">
        <div class="controls-start">
          ${pill(result.branch ? `Branch: ${result.branch}` : "Detached HEAD", "info", "branch")}
          ${pill(result.clean ? "Clean" : `${result.files.length} changed file${result.files.length === 1 ? "" : "s"}`, result.clean ? "good" : "warn", result.clean ? "spark" : "refresh")}
          ${hasRemote ? pill(ahead > 0 ? `${ahead} ahead` : "Up to date", ahead > 0 ? "warn" : "good", "publish") : pill("No upstream", "info", "branch")}
        </div>
        <div class="controls-end">
          ${renderOpenDropdown(snapshot.config.siteDir)}
        </div>
      </section>

      <section class="page-band">
        <div class="git-action-row">
          <button class="secondary-button" data-action="git-add-all" ${loading || unstagedTotal === 0 ? "disabled" : ""}>
            ${icon("publish")}
            <span>Add all</span>
          </button>
          <input class="git-commit-input" type="text" data-option="gitCommitMessage" value="${escapeHtml(state.gitCommitMessage)}" placeholder="Commit message" spellcheck="false" />
          <button class="primary-button" data-action="git-commit" ${loading || stagedFiles.length === 0 || state.gitCommitMessage.trim().length === 0 ? "disabled" : ""}>
            ${icon("spark")}
            <span>Commit</span>
          </button>
          <button class="secondary-button" data-action="git-push" ${loading || !hasRemote || ahead === 0 ? "disabled" : ""}>
            ${icon("publish")}
            <span>Push${ahead > 0 ? ` ${ahead}` : ""}</span>
          </button>
        </div>
        ${
          state.gitCommandResult
            ? `<div class="${state.gitCommandResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.gitCommandResult.output || state.gitCommandResult.error || "")}</div>`
            : ""
        }
      </section>

      <section class="page-band">
        <div class="git-columns">
          <div class="list-block">
            <div class="section-title">${icon("refresh")}<span>Changed Files</span></div>
            <div class="list">${filesHtml}</div>
          </div>
          <div class="list-block">
            <div class="section-title">${icon("branch")}<span>Recent Commits</span></div>
            <div class="list">${logHtml}</div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderSecretsModeTabs(active: "groups" | "containers"): string {
  return `
    <div class="segmented-tabs" role="tablist" aria-label="Secret views">
      <button class="segmented-tab${active === "groups" ? " active" : ""}" data-nav="secrets">
        ${icon("folder")}
        <span>Groups</span>
      </button>
      <button class="segmented-tab${active === "containers" ? " active" : ""}" data-nav="secrets:containers">
        ${icon("box")}
        <span>Containers</span>
      </button>
    </div>
  `;
}

function renderSecretsProviderBanner(): string {
  const providerStatus = state.secretsProviderStatus;
  const providerWarnings = [
    ...(state.secretsProviderError ? [state.secretsProviderError] : []),
    ...(providerStatus?.warnings ?? []),
  ];
  return state.secretsProviderLoading
    ? ""
    : providerWarnings.length > 0
      ? `<div class="error-band">${providerWarnings.map((warning) => escapeHtml(warning)).join("<br />")}</div>`
      : "";
}

function allNamedSecretGroups(snapshot: AppSnapshot): string[] {
  const providerDefinedGroups = state.secretsProviderStatus?.definedSecretGroups ?? [];
  return [...new Set([...snapshot.definedSecretGroups, ...providerDefinedGroups, ...snapshot.attachedSecretGroups])]
    .filter((group) => group !== "shared")
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: "accent" }));
}

function renderSecretsPage(snapshot: AppSnapshot): string {
  return renderSecretGroupsPage(snapshot);
}

function renderSecretGroupsPage(snapshot: AppSnapshot): string {
  const providerStatus = state.secretsProviderStatus;
  const providerDefinedGroups = providerStatus?.definedSecretGroups ?? [];
  const defined = new Set([...snapshot.definedSecretGroups, ...providerDefinedGroups]);
  const namedGroups = allNamedSecretGroups(snapshot);

  const groupCards = namedGroups
    .map((group) => {
      const isDefined = defined.has(group);
      const containers = snapshot.containers.filter((container) => container.secretGroups.includes(group));
      const containerLabel =
        containers.length > 0
          ? `${containers.length} container${containers.length === 1 ? "" : "s"}`
          : "No containers";
      return `
        <div class="secret-group-card">
          <button class="secret-group-card-main" data-nav="secrets:group:${escapeHtml(group)}">
            <span class="secret-group-card-title"><code>${escapeHtml(group)}</code></span>
            <span class="secret-group-card-meta">${escapeHtml(containerLabel)}</span>
            <span class="secret-group-card-status ${isDefined ? "configured" : "referenced"}">
              ${escapeHtml(isDefined ? "Configured" : "Referenced")}
            </span>
          </button>
          <div class="secret-group-card-actions">
            <button class="secondary-button compact-button" data-nav="secrets:group:${escapeHtml(group)}">Open</button>
            <button class="secondary-button compact-button" data-action="delete-secret-group" data-secret-group="${escapeHtml(group)}" ${state.secretTopologyRunning ? "disabled" : ""}>Delete</button>
          </div>
        </div>
      `;
    })
    .join("");

  return `
    <div class="page-stack">
      <section class="page-controls secrets-page-controls">
        <div class="controls-start">
          ${renderSecretsModeTabs("groups")}
        </div>
      </section>

      ${renderSecretsProviderBanner()}
      ${state.secretTopologyResult ? `<div class="${state.secretTopologyResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.secretTopologyResult.output || state.secretTopologyResult.error || "")}</div>` : ""}

      <section class="page-band">
        <button class="shared-secret-scope" data-nav="secrets:group:shared">
          <span class="shared-secret-icon">${icon("lock")}</span>
          <span class="shared-secret-copy">
            <span class="shared-secret-title">Shared</span>
            <span class="shared-secret-meta">Always configured and visible to every container automatically.</span>
          </span>
          <span class="shared-secret-action">Open</span>
        </button>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("folder")}<span>Named Groups</span></div>
            <div class="section-copy">
              Create groups, attach them to containers, then manage their secret stores.
            </div>
          </div>
          <div class="section-actions secret-create-actions">
            <input
              class="inline-action-input"
              type="text"
              data-option="secretGroupDraftName"
              value="${escapeHtml(state.secretGroupDraftName)}"
              placeholder="New group"
              spellcheck="false"
            />
            <button class="primary-button" data-action="create-secret-group" ${state.secretTopologyRunning || !state.secretGroupDraftName.trim() ? "disabled" : ""}>
              ${icon("publish")}
              <span>Create</span>
            </button>
          </div>
        </div>
        ${
          namedGroups.length > 0
            ? `<div class="secret-group-card-grid">${groupCards}</div>`
            : `<div class="empty-state">No named groups yet.</div>`
        }
      </section>
    </div>
  `;
}

function renderSecretContainersPage(snapshot: AppSnapshot): string {
  const providerStatus = state.secretsProviderStatus;
  const containerRows =
    snapshot.containers.length > 0
      ? snapshot.containers
          .map((container) => {
            const title = sidebarTitleFor(snapshot, container);
            const groups = container.secretGroups;
            const hasIdentity = effectiveContainerHasIdentity(container);
            const groupLabel = groups.length > 0 ? `Groups: ${groups.join(", ")}` : "No named groups";
            return `
              <div class="list-item">
                <div class="list-item-copy">
                  <div class="list-item-title">${escapeHtml(title)}</div>
                  <div class="list-item-meta">
                    ${escapeHtml(groupLabel)}
                    ${hasIdentity ? " &bull; Identity present" : ""}
                  </div>
                </div>
                <div class="nav-meta">
                  ${hasIdentity ? `<span class="nav-badge" title="Has age identity">K</span>` : ""}
                  ${groups.length > 0 ? `<span class="nav-badge" title="${escapeHtml(groups.join(", "))}">${groups.length}</span>` : ""}
                  <button class="secondary-button compact-button" data-nav="secrets:container:${escapeHtml(container.vmid)}">Manage</button>
                </div>
              </div>
            `;
          })
          .join("")
      : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No containers found.</div></div></div>`;

  return `
    <div class="page-stack">
      <section class="page-controls secrets-page-controls">
        <div class="controls-start">
          ${renderSecretsModeTabs("containers")}
        </div>
      </section>

      ${renderSecretsProviderBanner()}
      ${state.containerBundleResult ? `<div class="${state.containerBundleResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.containerBundleResult.output || state.containerBundleResult.error || "")}</div>` : ""}

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("box")}<span>Containers</span></div>
            <div class="section-copy">Create container bundles or open one to manage local secrets and its age identity.</div>
          </div>
          <div class="section-actions secret-create-actions">
            <input
              class="inline-action-input"
              type="text"
              data-option="containerBundleDraftVmid"
              value="${escapeHtml(state.containerBundleDraftVmid)}"
              placeholder="New VMID"
              spellcheck="false"
            />
            <button class="primary-button" data-action="create-container-bundle" ${state.containerBundleRunning || !state.containerBundleDraftVmid.trim() ? "disabled" : ""}>
              ${icon("box")}
              <span>Create</span>
            </button>
          </div>
        </div>
        <div class="list">${containerRows}</div>
      </section>
    </div>
  `;
}

function renderSecretScopePage(snapshot: AppSnapshot): string {
  const scope = secretScopeFromSelection();
  if (!scope) {
    return renderSecretsPage(snapshot);
  }

  const status = state.secretScopeStatus;
  const warnings = [
    ...(state.secretScopeError ? [state.secretScopeError] : []),
    ...(status?.warnings ?? []),
  ];
  const entries = status?.entries ?? [];
  const isContainer = scope.scopeType === "container";
  const container = isContainer
    ? snapshot.containers.find((candidate) => candidate.vmid === scope.scopeId)
    : null;
  const hasIdentity = container ? effectiveContainerHasIdentity(container) : false;
  const backSelection = isContainer ? "secrets:containers" : "secrets";

  const listHtml = state.secretScopeLoading
    ? `<div class="running-band">${icon("refresh")}<span>Loading secrets...</span></div>`
    : entries.length > 0
      ? entries
          .map((entry) => `
            <div class="list-item">
              <div class="list-item-copy">
                <div class="list-item-title"><code>${escapeHtml(entry.name)}</code></div>
                <div class="list-item-meta">${escapeHtml(entry.source)}</div>
              </div>
              <button
                class="secondary-button compact-button"
                data-action="remove-secret"
                data-secret-name="${escapeHtml(entry.name)}"
                ${isContainer && entry.source !== "container" ? "disabled" : ""}
              >
                Remove
              </button>
            </div>
          `)
          .join("")
      : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No secret names found.</div></div></div>`;

  return `
    <div class="page-stack">
      <section class="page-controls">
        <div class="controls-start">
          <button class="secondary-button" data-nav="${backSelection}">
            ${icon("back")}
            <span>Back</span>
          </button>
          ${isContainer ? pill(hasIdentity ? "Identity present" : "No identity", hasIdentity ? "good" : "warn", "key") : ""}
        </div>
        <div class="controls-end">
          ${isContainer ? `<button class="secondary-button" data-action="init-container-identity" ${state.secretScopeRunning ? "disabled" : ""}>${icon("key")}<span>Init Identity</span></button>` : ""}
          <button class="secondary-button" data-action="rotate-secret-scope" ${state.secretScopeRunning || !(status?.canRotate ?? usesEmbeddedSops(snapshot.config.secretProvider)) ? "disabled" : ""}>
            ${icon("refresh")}
            <span>Rotate</span>
          </button>
        </div>
      </section>

      ${warnings.length > 0 ? `<div class="error-band">${warnings.map((warning) => escapeHtml(warning)).join("<br />")}</div>` : ""}
      ${state.secretScopeResult ? `<div class="${state.secretScopeResult.exitCode === 0 ? "success-band" : "error-band"}">${escapeHtml(state.secretScopeResult.output || state.secretScopeResult.error || "")}</div>` : ""}

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("lock")}<span>Write Secret</span></div>
            <div class="section-copy">Values are sent to proxnix-secrets and are not shown after saving.</div>
          </div>
          <div class="section-actions">
            <button class="primary-button" data-action="set-secret" ${state.secretScopeRunning || !state.secretDraftName.trim() || !state.secretDraftValue ? "disabled" : ""}>
              ${icon("publish")}
              <span>Set</span>
            </button>
          </div>
        </div>
        <div class="form-grid">
          ${renderSettingsField("Name", "secretDraftName", state.secretDraftName, "Secret key in this scope.", undefined, false, false, "data-option")}
          <label class="field">
            <div class="field-label-row">
              <span class="field-label">Value</span>
            </div>
            <div class="field-control">
              <input type="password" data-secret-value="secretDraftValue" value="${escapeHtml(state.secretDraftValue)}" spellcheck="false" />
            </div>
            <div class="field-hint">New value to write.</div>
          </label>
        </div>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("key")}<span>Secret Names</span></div>
            <div class="section-copy">Container views include inherited shared and group entries with their source.</div>
          </div>
        </div>
        <div class="list">${listHtml}</div>
      </section>
    </div>
  `;
}

function renderMain(snapshot: AppSnapshot): string {
  if (snapshot.config.siteDir.length === 0 && state.selection !== "settings") {
    return renderOnboarding(snapshot);
  }

  if (state.selection === "settings") {
    return renderSettingsForm(snapshot);
  }

  if (state.selection === "welcome") {
    return renderWelcomePage(snapshot);
  }

  if (state.selection === "site") {
    return renderSitePage(snapshot);
  }

  if (state.selection.startsWith("container:")) {
    const container = selectedContainer();
    if (!container) {
      return `
        <div class="page-stack">
          <section class="hero-band">
            <div class="hero-copy">
              <div class="hero-title">Container not found</div>
              <div class="hero-text">
                This VMID no longer exists in the site. Refresh or select another container.
              </div>
            </div>
          </section>
        </div>
      `;
    }
    return renderContainerPage(container);
  }

  if (state.selection === "publish") {
    return renderPublishPage(snapshot);
  }

  if (state.selection === "secrets") {
    return renderSecretsPage(snapshot);
  }

  if (state.selection === "secrets:groups") {
    return renderSecretGroupsPage(snapshot);
  }

  if (state.selection === "secrets:containers") {
    return renderSecretContainersPage(snapshot);
  }

  if (state.selection.startsWith("secrets:group:") || state.selection.startsWith("secrets:container:")) {
    return renderSecretScopePage(snapshot);
  }

  if (state.selection === "doctor") {
    return renderDoctorPage(snapshot);
  }

  return renderGitPage(snapshot);
}

function renderStatusbar(snapshot: AppSnapshot): string {
  const isSecretsPage = state.selection === "secrets" || state.selection.startsWith("secrets:");
  const containerLabel = selectedContainerStatusbarLabel();
  const leftLabel = containerLabel
    ? containerLabel
    : isSecretsPage
      ? `Secrets: <code>${escapeHtml(snapshot.config.secretProvider)}</code>`
      : snapshot.config.siteDir
        ? `Site: <code>${escapeHtml(compactPath(snapshot.config.siteDir))}</code>`
        : "Site: Not configured";
  const activityLabel =
    state.saving
      ? "Saving config"
      : state.metadataSaving
        ? "Saving sidebar"
        : state.loading
          ? "Refreshing state"
          : state.onboardingRunning
            ? "Creating site"
          : state.publishRunning
            ? "Publishing"
            : state.doctorRunning
              ? "Running doctor"
              : state.gitRunning
                ? "Updating git"
                : state.gitLoading
                  ? "Loading git"
                  : state.secretScopeRunning
                    ? "Updating secrets"
                    : state.secretScopeLoading || state.secretsProviderLoading
                      ? "Refreshing secrets"
                      : null;

  return `
    <footer class="statusbar">
      <div class="statusbar-meta">
        <span>${leftLabel}</span>
      </div>
      <div class="statusbar-meta statusbar-activity-slot">
        ${
          activityLabel
            ? `<span class="statusbar-activity">${icon("refresh")}<span>${escapeHtml(activityLabel)}</span></span>`
            : ""
        }
      </div>
    </footer>
  `;
}

function renderFilePreview(): string {
  if (!state.filePreview && !state.filePreviewLoading) {
    return "";
  }

  return `
    <div class="modal-backdrop">
      <section class="file-preview-modal" role="dialog" aria-modal="true" aria-label="File preview">
        <div class="file-preview-header">
          <div>
            <div class="file-preview-title">${escapeHtml(state.filePreview?.title ?? "Loading preview...")}</div>
            <div class="file-preview-path">${escapeHtml(state.filePreview?.path ?? "")}</div>
          </div>
          <button class="secondary-button compact-button" data-action="close-file-preview">
            <span>Close</span>
          </button>
        </div>
        <pre class="file-preview-content">${escapeHtml(state.filePreviewLoading ? "Loading..." : state.filePreview?.content ?? "")}</pre>
      </section>
    </div>
  `;
}

function render(): void {
  if (state.loading && !state.snapshot) {
    root.innerHTML = `<div class="loading-state">Loading proxnix workstation state...</div>`;
    return;
  }

  const snapshot = state.snapshot;
  if (!snapshot) {
    root.innerHTML = `
      <div class="loading-state">
        <div>No proxnix state available.</div>
        ${
          state.error
            ? `<div class="error-band">${escapeHtml(state.error)}</div>`
            : ""
        }
        <div class="hero-actions">
          <button class="primary-button" data-action="retry-load">
            ${icon("refresh")}
            <span>Retry</span>
          </button>
        </div>
      </div>
    `;
    return;
  }

  root.innerHTML = `
    <div class="shell">
      ${renderSidebar(snapshot)}
      <main class="main">
        ${renderToolbar(snapshot)}
        ${renderWarnings(snapshot)}
        <div class="page-scroll">
          ${renderMain(snapshot)}
        </div>
        ${renderStatusbar(snapshot)}
      </main>
    </div>
    ${renderFilePreview()}
  `;
  syncDraftIndicators();
  syncSidebarMetadataIndicators();
  syncSecretDraftIndicators();
  syncSecretTopologyIndicators();
  syncContainerBundleIndicators();
}

async function refreshSnapshot(attempt = 0, force = false): Promise<void> {
  state.loading = true;
  if (attempt === 0) {
    state.error = null;
    if (force) {
      clearSecretsProviderStatus();
      clearSecretScopeStatus();
    }
  }
  render();

  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.loadSnapshot({ force }));
    const nextSecretsKey = secretsProviderCacheKey(snapshot);
    if (
      state.secretsProviderLoadedKey !== nextSecretsKey &&
      state.secretsProviderLoadingKey !== nextSecretsKey
    ) {
      clearSecretsProviderStatus();
    }
    syncExpandedGroups(snapshot);
    state.snapshot = snapshot;
    state.draft = cloneConfig(snapshot.config);
    ensureSelection(snapshot);
    syncContainerMetadataDraft(snapshot);
    if (isSecretsIndexSelection()) {
      void ensureSecretsProviderStatus(force);
    }
    if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
      void ensureSecretScopeStatus(force);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!state.snapshot && attempt < 4) {
      state.error = `Initial load failed (${attempt + 1}/5): ${message}`;
      render();
      await sleep(250 * (attempt + 1));
      return refreshSnapshot(attempt + 1, force);
    }
    state.error = message;
  } finally {
    state.loading = false;
    render();
  }
}

async function handleRunDoctor(): Promise<void> {
  state.doctorRunning = true;
  state.doctorResult = null;
  render();

  try {
    state.doctorResult = await proxnixRpc.request.runDoctor({
      configOnly: state.doctorConfigOnly || undefined,
      vmid: state.doctorVmid || undefined,
    });
  } catch (error) {
    state.doctorResult = {
      sections: [],
      oks: 0,
      warns: 0,
      fails: 0,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.doctorRunning = false;
    render();
  }
}

async function handleRunPublish(dryRun: boolean): Promise<void> {
  state.publishRunning = true;
  state.publishResult = null;
  render();

  try {
    state.publishResult = await proxnixRpc.request.runPublish({
      dryRun,
      configOnly: state.publishConfigOnly || undefined,
      vmid: state.publishVmid || undefined,
    });
  } catch (error) {
    state.publishResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.publishRunning = false;
    render();
  }
}

async function handleCreateSiteNix(): Promise<void> {
  state.siteNixResult = null;
  render();

  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.createSiteNix());
    installMutatedSnapshot(snapshot);
    state.siteNixResult = { output: "Created site.nix.", exitCode: 0 };
  } catch (error) {
    state.siteNixResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    render();
  }
}

async function handleRunOnboarding(): Promise<void> {
  if (!state.draft) {
    return;
  }

  const config = onboardingConfig();
  if (!canRunOnboarding(config)) {
    return;
  }

  state.onboardingRunning = true;
  state.onboardingResult = null;
  state.error = null;
  render();

  try {
    const result = await proxnixRpc.request.runOnboarding({ config });
    const snapshot = normalizeSnapshot(result.snapshot);
    installMutatedSnapshot(snapshot);
    state.onboardingResult = {
      output: result.output,
      exitCode: 0,
    };
    state.selection = "welcome";
    void ensureSecretsProviderStatus(true);
    void handleRefreshGit();
  } catch (error) {
    state.onboardingResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.onboardingRunning = false;
    render();
  }
}

async function handleRefreshGit(): Promise<void> {
  state.gitLoading = true;
  render();

  try {
    state.gitResult = await proxnixRpc.request.gitStatus();
  } catch (error) {
    state.gitResult = {
      branch: "",
      clean: true,
      files: [],
      log: [],
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.gitLoading = false;
    render();
  }
}

async function handleGitAdd(path?: string): Promise<void> {
  state.gitRunning = true;
  state.gitCommandResult = null;
  render();

  try {
    state.gitCommandResult = await proxnixRpc.request.gitAdd(
      path ? { file: path } : { all: true },
    );
    await handleRefreshGit();
  } catch (error) {
    state.gitCommandResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.gitRunning = false;
    render();
  }
}

async function handleGitCommit(): Promise<void> {
  const message = state.gitCommitMessage.trim();
  if (!message) {
    return;
  }

  state.gitRunning = true;
  state.gitCommandResult = null;
  render();

  try {
    state.gitCommandResult = await proxnixRpc.request.gitCommit({ message });
    if (state.gitCommandResult.exitCode === 0) {
      state.gitCommitMessage = "";
    }
    await handleRefreshGit();
  } catch (error) {
    state.gitCommandResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.gitRunning = false;
    render();
  }
}

async function handleGitPush(): Promise<void> {
  state.gitRunning = true;
  state.gitCommandResult = null;
  render();

  try {
    state.gitCommandResult = await proxnixRpc.request.gitPush();
    await handleRefreshGit();
  } catch (error) {
    state.gitCommandResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.gitRunning = false;
    render();
  }
}

async function refreshAfterSecretMutation(): Promise<void> {
  await refreshSnapshot(0, true);
  if (isSecretsIndexSelection()) {
    void ensureSecretsProviderStatus(true);
  }
  if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
    await ensureSecretScopeStatus(true);
  }
}

function installMutatedSnapshot(snapshot: AppSnapshot): void {
  const nextSecretsKey = secretsProviderCacheKey(snapshot);
  if (
    state.secretsProviderLoadedKey !== nextSecretsKey &&
    state.secretsProviderLoadingKey !== nextSecretsKey
  ) {
    clearSecretsProviderStatus();
  }
  syncExpandedGroups(snapshot);
  state.snapshot = snapshot;
  state.draft = cloneConfig(snapshot.config);
  ensureSelection(snapshot);
  syncContainerMetadataDraft(snapshot);
}

async function refreshAfterSecretTopologyMutation(snapshot: AppSnapshot): Promise<void> {
  installMutatedSnapshot(snapshot);
  if (isSecretsIndexSelection()) {
    void ensureSecretsProviderStatus(true);
  }
  if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
    await ensureSecretScopeStatus(true);
  }
}

async function refreshAfterContainerBundleMutation(snapshot: AppSnapshot): Promise<void> {
  installMutatedSnapshot(snapshot);
  void ensureSecretsProviderStatus(true);
  if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
    await ensureSecretScopeStatus(true);
  }
}

async function handleCreateContainerBundle(): Promise<void> {
  const vmid = state.containerBundleDraftVmid.trim();
  if (!vmid) {
    return;
  }

  state.containerBundleRunning = true;
  state.containerBundleResult = null;
  state.pendingDeleteContainerBundleVmid = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.createContainerBundle({ vmid }));
    state.containerBundleDraftVmid = "";
    state.containerBundleResult = { output: `Created container bundle ${vmid} and initialized its identity.`, exitCode: 0 };
    state.selection = `container:${vmid}`;
    await refreshAfterContainerBundleMutation(snapshot);
  } catch (error) {
    state.containerBundleResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.containerBundleRunning = false;
    render();
  }
}

async function handleDeleteContainerBundle(vmid: string): Promise<void> {
  if (!vmid) {
    return;
  }
  if (state.pendingDeleteContainerBundleVmid !== vmid) {
    state.pendingDeleteContainerBundleVmid = vmid;
    render();
    return;
  }

  state.containerBundleRunning = true;
  state.containerBundleResult = null;
  state.pendingDeleteContainerBundleVmid = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.deleteContainerBundle({ vmid }));
    if (state.selection === `container:${vmid}` || state.selection === `secrets:container:${vmid}`) {
      state.selection = "secrets:containers";
    }
    state.containerBundleResult = { output: `Deleted container bundle ${vmid} and removed its identity.`, exitCode: 0 };
    await refreshAfterContainerBundleMutation(snapshot);
  } catch (error) {
    state.containerBundleResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.containerBundleRunning = false;
    render();
  }
}

async function handleCreateSecretGroup(): Promise<void> {
  const group = state.secretGroupDraftName.trim();
  if (!group) {
    return;
  }

  state.secretTopologyRunning = true;
  state.secretTopologyResult = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.createSecretGroup({ group }));
    state.secretGroupDraftName = "";
    state.secretTopologyResult = { output: `Created group ${group}.`, exitCode: 0 };
    await refreshAfterSecretTopologyMutation(snapshot);
  } catch (error) {
    state.secretTopologyResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretTopologyRunning = false;
    render();
  }
}

async function handleDeleteSecretGroup(group: string): Promise<void> {
  if (!group) {
    return;
  }
  if (!window.confirm(`Delete group ${group} and detach it from all containers? Secret stores are not deleted.`)) {
    return;
  }

  state.secretTopologyRunning = true;
  state.secretTopologyResult = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.deleteSecretGroup({ group }));
    if (state.selection === `secrets:group:${group}`) {
      state.selection = "secrets";
    }
    state.secretTopologyResult = { output: `Deleted group ${group}.`, exitCode: 0 };
    await refreshAfterSecretTopologyMutation(snapshot);
  } catch (error) {
    state.secretTopologyResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretTopologyRunning = false;
    render();
  }
}

async function handleAttachSecretGroup(vmid: string): Promise<void> {
  const group = state.containerSecretGroupDraft.trim();
  if (!vmid || !group) {
    return;
  }

  state.secretTopologyRunning = true;
  state.secretTopologyResult = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.attachSecretGroup({ vmid, group }));
    state.containerSecretGroupDraft = "";
    state.secretTopologyResult = { output: `Attached ${group} to VMID ${vmid}.`, exitCode: 0 };
    await refreshAfterSecretTopologyMutation(snapshot);
  } catch (error) {
    state.secretTopologyResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretTopologyRunning = false;
    render();
  }
}

async function handleDetachSecretGroup(vmid: string, group: string): Promise<void> {
  if (!vmid || !group) {
    return;
  }

  state.secretTopologyRunning = true;
  state.secretTopologyResult = null;
  render();
  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.detachSecretGroup({ vmid, group }));
    state.secretTopologyResult = { output: `Detached ${group} from VMID ${vmid}.`, exitCode: 0 };
    await refreshAfterSecretTopologyMutation(snapshot);
  } catch (error) {
    state.secretTopologyResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretTopologyRunning = false;
    render();
  }
}

async function handleSetSecret(): Promise<void> {
  const scope = secretScopeFromSelection();
  if (!scope) {
    return;
  }

  state.secretScopeRunning = true;
  state.secretScopeResult = null;
  render();
  try {
    state.secretScopeResult = await proxnixRpc.request.setSecret({
      scopeType: scope.scopeType,
      scopeId: scope.scopeType === "shared" ? undefined : scope.scopeId,
      name: state.secretDraftName.trim(),
      value: state.secretDraftValue,
    });
    if (state.secretScopeResult.exitCode === 0) {
      state.secretDraftName = "";
      state.secretDraftValue = "";
      await refreshAfterSecretMutation();
    }
  } catch (error) {
    state.secretScopeResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretScopeRunning = false;
    render();
  }
}

async function handleRemoveSecret(name: string): Promise<void> {
  const scope = secretScopeFromSelection();
  if (!scope || !name) {
    return;
  }

  state.secretScopeRunning = true;
  state.secretScopeResult = null;
  render();
  try {
    state.secretScopeResult = await proxnixRpc.request.removeSecret({
      scopeType: scope.scopeType,
      scopeId: scope.scopeType === "shared" ? undefined : scope.scopeId,
      name,
    });
    if (state.secretScopeResult.exitCode === 0) {
      await refreshAfterSecretMutation();
    }
  } catch (error) {
    state.secretScopeResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretScopeRunning = false;
    render();
  }
}

async function handleRotateSecretScope(): Promise<void> {
  const scope = secretScopeFromSelection();
  if (!scope) {
    return;
  }

  state.secretScopeRunning = true;
  state.secretScopeResult = null;
  render();
  try {
    state.secretScopeResult = await proxnixRpc.request.rotateSecretScope({
      scopeType: scope.scopeType,
      scopeId: scope.scopeType === "shared" ? undefined : scope.scopeId,
    });
    await refreshAfterSecretMutation();
  } catch (error) {
    state.secretScopeResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretScopeRunning = false;
    render();
  }
}

async function handleInitContainerIdentity(): Promise<void> {
  const scope = secretScopeFromSelection();
  if (!scope || scope.scopeType !== "container") {
    return;
  }

  state.secretScopeRunning = true;
  state.secretScopeResult = null;
  render();
  try {
    state.secretScopeResult = await proxnixRpc.request.initContainerIdentity({ vmid: scope.scopeId });
    await refreshAfterSecretMutation();
  } catch (error) {
    state.secretScopeResult = {
      output: "",
      exitCode: 1,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.secretScopeRunning = false;
    render();
  }
}

async function handlePreviewFile(path: string, title: string): Promise<void> {
  if (!path) {
    return;
  }
  state.filePreview = { path, title, content: "" };
  state.filePreviewLoading = true;
  render();
  try {
    const preview = await proxnixRpc.request.readTextFile({ path });
    state.filePreview = { ...preview, title };
  } catch (error) {
    state.filePreview = {
      path,
      title,
      content: error instanceof Error ? error.message : String(error),
    };
  } finally {
    state.filePreviewLoading = false;
    render();
  }
}

async function handleAction(action: string, element: HTMLElement): Promise<void> {
  if (action === "retry-load") {
    await refreshSnapshot(0, true);
    return;
  }

  if (!state.snapshot) {
    return;
  }

  if (action === "refresh") {
    state.pendingDeleteContainerBundleVmid = null;
    await refreshSnapshot(0, true);
    if (state.selection === "git") {
      await handleRefreshGit();
    }
    return;
  }

  if (action === "run-doctor") {
    await handleRunDoctor();
    return;
  }

  if (action === "publish-preview") {
    await handleRunPublish(true);
    return;
  }

  if (action === "publish-execute") {
    await handleRunPublish(false);
    return;
  }

  if (action === "create-site-nix") {
    await handleCreateSiteNix();
    return;
  }

  if (action === "run-onboarding") {
    await handleRunOnboarding();
    return;
  }

  if (action === "set-secret") {
    await handleSetSecret();
    return;
  }

  if (action === "remove-secret") {
    await handleRemoveSecret(element.dataset.secretName ?? "");
    return;
  }

  if (action === "rotate-secret-scope") {
    await handleRotateSecretScope();
    return;
  }

  if (action === "init-container-identity") {
    await handleInitContainerIdentity();
    return;
  }

  if (action === "create-container-bundle") {
    await handleCreateContainerBundle();
    return;
  }

  if (action === "delete-container-bundle") {
    await handleDeleteContainerBundle(element.dataset.vmid ?? "");
    return;
  }

  if (action === "create-secret-group") {
    await handleCreateSecretGroup();
    return;
  }

  if (action === "delete-secret-group") {
    await handleDeleteSecretGroup(element.dataset.secretGroup ?? "");
    return;
  }

  if (action === "attach-secret-group") {
    await handleAttachSecretGroup(element.dataset.vmid ?? "");
    return;
  }

  if (action === "detach-secret-group") {
    await handleDetachSecretGroup(element.dataset.vmid ?? "", element.dataset.secretGroup ?? "");
    return;
  }

  if (action === "preview-file") {
    await handlePreviewFile(element.dataset.path ?? "", element.dataset.previewTitle ?? "Preview");
    return;
  }

  if (action === "close-file-preview") {
    state.filePreview = null;
    state.filePreviewLoading = false;
    render();
    return;
  }

  if (action === "git-add-all") {
    await handleGitAdd();
    return;
  }

  if (action === "git-add-file") {
    const path = element.dataset.gitPath;
    if (path) {
      await handleGitAdd(path);
    }
    return;
  }

  if (action === "git-commit") {
    await handleGitCommit();
    return;
  }

  if (action === "git-push") {
    await handleGitPush();
    return;
  }

  if (action === "open-site") {
    if (state.snapshot.config.siteDir) {
      await proxnixRpc.request.openPath({ path: state.snapshot.config.siteDir });
    }
    return;
  }

  if (action === "open-config") {
    await proxnixRpc.request.openPath({ path: state.snapshot.configPath });
    return;
  }

  if (action === "open-path") {
    const path = element.dataset.path;
    if (path) {
      await proxnixRpc.request.openPath({ path });
    }
    return;
  }

  if (action === "open-in-editor") {
    const path = element.dataset.path;
    if (path) {
      try {
        const result = await proxnixRpc.request.openInEditor({ path });
        if (!result.opened && result.error) {
          state.error = result.error;
          render();
        }
      } catch (error) {
        state.error = error instanceof Error ? error.message : String(error);
        render();
      }
    }
    return;
  }

  if (action === "choose-site") {
    const startingFolder = state.draft?.siteDir || state.snapshot.config.siteDir;
    const chosen = await proxnixRpc.request.chooseSiteDirectory({
      startingFolder: startingFolder || undefined,
    });

    if (chosen && state.draft) {
      state.draft.siteDir = chosen;
      render();
    }
    return;
  }

  if (action === "reset-draft") {
    state.draft = cloneConfig(state.snapshot.config);
    state.error = null;
    render();
    return;
  }

  if (action === "save-config") {
    if (!state.draft || !isDirty()) {
      return;
    }

    state.saving = true;
    state.error = null;
    render();

    try {
      const snapshot = normalizeSnapshot(await proxnixRpc.request.saveConfig({ config: state.draft }));
      const nextSecretsKey = secretsProviderCacheKey(snapshot);
      if (
        state.secretsProviderLoadedKey !== nextSecretsKey &&
        state.secretsProviderLoadingKey !== nextSecretsKey
      ) {
        clearSecretsProviderStatus();
      }
      syncExpandedGroups(snapshot);
      state.snapshot = snapshot;
      state.draft = cloneConfig(snapshot.config);
      ensureSelection(snapshot);
      syncContainerMetadataDraft(snapshot);
      if (isSecretsIndexSelection()) {
        void ensureSecretsProviderStatus();
      }
      if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
        void ensureSecretScopeStatus(true);
      }
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error);
    } finally {
      state.saving = false;
      render();
    }
  }

  if (action === "reset-sidebar-metadata") {
    const snapshot = state.snapshot;
    const container = selectedContainer();
    if (!snapshot || !container) {
      return;
    }
    state.containerMetadataDraft = currentContainerSidebarMetadata(snapshot, container.vmid);
    state.sidebarGroupInput = "";
    state.sidebarLabelInput = "";
    render();
    return;
  }

  if (action === "clear-sidebar-metadata") {
    state.containerMetadataDraft = defaultSidebarMetadata();
    state.sidebarGroupInput = "";
    state.sidebarLabelInput = "";
    render();
    return;
  }

  if (action === "toggle-display-settings") {
    state.displaySettingsOpen = !state.displaySettingsOpen;
    render();
    return;
  }

  if (action === "set-sidebar-group") {
    if (!state.containerMetadataDraft) {
      return;
    }
    state.containerMetadataDraft.group = element.dataset.groupValue ?? "";
    state.sidebarGroupInput = "";
    syncSidebarMetadataIndicators();
    render();
    return;
  }

  if (action === "add-sidebar-label") {
    addSidebarLabel(element.dataset.labelValue ?? "");
    state.sidebarLabelInput = "";
    syncSidebarMetadataIndicators();
    render();
    return;
  }

  if (action === "remove-sidebar-label") {
    removeSidebarLabel(element.dataset.labelValue ?? "");
    syncSidebarMetadataIndicators();
    render();
    return;
  }

  if (action === "save-sidebar-metadata") {
    const container = selectedContainer();
    if (!container || !state.containerMetadataDraft || !state.snapshot) {
      return;
    }
    commitSidebarPickerInputs();

    state.metadataSaving = true;
    state.error = null;
    syncSidebarMetadataIndicators();

    try {
      const snapshot = normalizeSnapshot(
        await proxnixRpc.request.saveSidebarMetadata({
          vmid: container.vmid,
          metadata: state.containerMetadataDraft,
        }),
      );
      const nextSecretsKey = secretsProviderCacheKey(snapshot);
      if (
        state.secretsProviderLoadedKey !== nextSecretsKey &&
        state.secretsProviderLoadingKey !== nextSecretsKey
      ) {
        clearSecretsProviderStatus();
      }
      syncExpandedGroups(snapshot);
      state.snapshot = snapshot;
      state.draft = cloneConfig(snapshot.config);
      ensureSelection(snapshot);
      syncContainerMetadataDraft(snapshot);
      if (isSecretsIndexSelection()) {
        void ensureSecretsProviderStatus();
      }
      if (state.selection.startsWith("secrets:") || state.selection.startsWith("container:")) {
        void ensureSecretScopeStatus(true);
      }
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error);
    } finally {
      state.metadataSaving = false;
      render();
    }
  }
}

function closeAllDropdowns(): void {
  for (const menu of root.querySelectorAll<HTMLElement>(".dropdown.open")) {
    menu.classList.remove("open");
  }
}

root.addEventListener("click", (event) => {
  const target = event.target as HTMLElement | null;

  const dropdownToggle = target?.closest<HTMLElement>("[data-dropdown-toggle]");
  if (dropdownToggle) {
    const dropdown = dropdownToggle.closest<HTMLElement>(".dropdown");
    if (dropdown) {
      const wasOpen = dropdown.classList.contains("open");
      closeAllDropdowns();
      if (!wasOpen) {
        dropdown.classList.add("open");
      }
      return;
    }
  }

  if (!target?.closest(".dropdown-menu")) {
    closeAllDropdowns();
  }

  const groupToggle = target?.closest<HTMLElement>("[data-group-toggle]");
  if (groupToggle?.dataset.groupToggle) {
    const groupId = groupToggle.dataset.groupToggle;
    if (state.expandedGroups.has(groupId)) {
      state.expandedGroups.delete(groupId);
    } else {
      state.expandedGroups.add(groupId);
    }
    render();
    return;
  }

  const navButton = target?.closest<HTMLElement>("[data-nav]");
  if (navButton?.dataset.nav) {
    setSelection(navButton.dataset.nav as ViewSelection);
    return;
  }

  const actionButton = target?.closest<HTMLElement>("[data-action]");
  if (actionButton?.dataset.action) {
    closeAllDropdowns();
    void handleAction(actionButton.dataset.action, actionButton);
  }
});

function updateDraftFromField(target: HTMLInputElement | HTMLSelectElement): void {
  if (!state.draft) {
    return;
  }

  const field = target.dataset.field as keyof ProxnixConfig | undefined;
  if (!field) {
    return;
  }

  state.draft[field] = target.value;
  syncDraftIndicators();
}

function updateContainerMetadataFromField(target: HTMLInputElement): void {
  if (!state.containerMetadataDraft) {
    return;
  }

  const field = target.dataset.containerField;
  if (field === "displayName" || field === "group") {
    state.containerMetadataDraft[field] = target.value;
    syncSidebarMetadataIndicators();
    return;
  }

  if (field === "labels") {
    state.containerMetadataDraft.labels = parseSidebarLabels(target.value);
    syncSidebarMetadataIndicators();
  }
}

function addSidebarLabel(label: string): void {
  if (!state.containerMetadataDraft) {
    return;
  }
  const normalized = label.trim();
  if (!normalized) {
    return;
  }
  const existing = new Set(state.containerMetadataDraft.labels.map((value) => value.toLocaleLowerCase()));
  if (!existing.has(normalized.toLocaleLowerCase())) {
    state.containerMetadataDraft.labels = [...state.containerMetadataDraft.labels, normalized];
  }
}

function removeSidebarLabel(label: string): void {
  if (!state.containerMetadataDraft) {
    return;
  }
  const normalized = label.trim().toLocaleLowerCase();
  state.containerMetadataDraft.labels = state.containerMetadataDraft.labels.filter(
    (value) => value.toLocaleLowerCase() !== normalized,
  );
}

function commitSidebarPickerInputs(): void {
  if (!state.containerMetadataDraft) {
    return;
  }
  const group = state.sidebarGroupInput.trim();
  if (group) {
    state.containerMetadataDraft.group = group;
    state.sidebarGroupInput = "";
  }
  for (const label of parseSidebarLabels(state.sidebarLabelInput)) {
    addSidebarLabel(label);
  }
  state.sidebarLabelInput = "";
  syncSidebarMetadataIndicators();
}

function updateOptionFromField(target: HTMLInputElement): void {
  const option = target.dataset.option;
  if (!option) return;

  if (target.type === "checkbox") {
    if (option === "doctorConfigOnly") state.doctorConfigOnly = target.checked;
    if (option === "publishConfigOnly") state.publishConfigOnly = target.checked;
  } else {
    if (option === "doctorVmid") state.doctorVmid = target.value;
    if (option === "publishVmid") state.publishVmid = target.value;
    if (option === "gitCommitMessage") state.gitCommitMessage = target.value;
    if (option === "secretDraftName") state.secretDraftName = target.value;
    if (option === "secretGroupDraftName") state.secretGroupDraftName = target.value;
    if (option === "containerSecretGroupDraft") state.containerSecretGroupDraft = target.value;
    if (option === "containerBundleDraftVmid") state.containerBundleDraftVmid = target.value;
  }
}

root.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    if (target.dataset.secretValue) {
      state.secretDraftValue = target.value;
      syncSecretDraftIndicators();
    } else if (target.dataset.sidebarGroupInput) {
      state.sidebarGroupInput = target.value;
      syncSidebarMetadataIndicators();
    } else if (target.dataset.sidebarLabelInput) {
      state.sidebarLabelInput = target.value;
      syncSidebarMetadataIndicators();
    } else if (target.dataset.option) {
      updateOptionFromField(target);
      if (target.dataset.option === "secretDraftName") {
        syncSecretDraftIndicators();
      }
      if (
        target.dataset.option === "secretGroupDraftName" ||
        target.dataset.option === "containerSecretGroupDraft"
      ) {
        syncSecretTopologyIndicators();
      }
      if (target.dataset.option === "containerBundleDraftVmid") {
        syncContainerBundleIndicators();
      }
    } else if (target.dataset.containerField) {
      updateContainerMetadataFromField(target);
    } else {
      updateDraftFromField(target);
    }
  }
});

root.addEventListener("keydown", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) {
    return;
  }

  if (target.dataset.sidebarGroupInput && event.key === "Enter") {
    event.preventDefault();
    commitSidebarPickerInputs();
    render();
    return;
  }

  if (target.dataset.sidebarLabelInput && (event.key === "Enter" || event.key === ",")) {
    event.preventDefault();
    commitSidebarPickerInputs();
    render();
    return;
  }

  if (target.dataset.option === "secretGroupDraftName" && event.key === "Enter") {
    event.preventDefault();
    void handleCreateSecretGroup();
    return;
  }

  if (target.dataset.option === "containerSecretGroupDraft" && event.key === "Enter") {
    event.preventDefault();
    const container = selectedContainer();
    if (container) {
      void handleAttachSecretGroup(container.vmid);
    }
    return;
  }

  if (target.dataset.option === "containerBundleDraftVmid" && event.key === "Enter") {
    event.preventDefault();
    void handleCreateContainerBundle();
  }
});

root.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement) {
    const field = target.dataset.field;
    updateDraftFromField(target);
    if (field === "secretProvider") {
      render();
    }
  } else if (target instanceof HTMLInputElement && target.type === "checkbox" && target.dataset.option) {
    updateOptionFromField(target);
  }
});

void refreshSnapshot();
