import { Electroview } from "electrobun/view";
import type {
  AppSnapshot,
  CommandResult,
  ContainerSummary,
  DoctorResult,
  GitStatusResult,
  ProxnixConfig,
  ProxnixManagerRPC,
  SidebarMetadata,
} from "../shared/types";

type ViewSelection =
  | "settings"
  | "publish"
  | "secrets"
  | "doctor"
  | "git"
  | `container:${string}`;

type IconName =
  | "box"
  | "branch"
  | "chevron"
  | "edit"
  | "folder"
  | "gear"
  | "health"
  | "key"
  | "lock"
  | "open"
  | "publish"
  | "refresh"
  | "spark";

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

const proxnixRpc = Electroview.defineRPC<ProxnixManagerRPC>({
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
} = {
  snapshot: null,
  draft: null,
  containerMetadataDraft: null,
  expandedGroups: new Set<string>(),
  expandedGroupsInitialized: false,
  selection: "settings",
  loading: true,
  saving: false,
  metadataSaving: false,
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
};

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
  return metadata.displayName || container.vmid;
}

function currentContainerSidebarMetadata(snapshot: AppSnapshot, vmid: string): SidebarMetadata {
  return cloneSidebarMetadata(sidebarMetadataFor(snapshot, vmid));
}

function syncContainerMetadataDraft(snapshot: AppSnapshot): void {
  const container = selectedContainer();
  state.containerMetadataDraft = container
    ? currentContainerSidebarMetadata(snapshot, container.vmid)
    : null;
}

function setSelection(next: ViewSelection): void {
  state.selection = next;
  if (state.snapshot) {
    syncContainerMetadataDraft(state.snapshot);
  }
  render();
  if (next === "git" && !state.gitResult && !state.gitLoading) {
    void handleRefreshGit();
  }
}

function ensureSelection(snapshot: AppSnapshot): void {
  if (snapshot.config.siteDir.length === 0) {
    state.selection = "settings";
    state.containerMetadataDraft = null;
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
    state.selection === "publish" ||
    state.selection === "secrets" ||
    state.selection === "doctor" ||
    state.selection === "git"
  ) {
    state.containerMetadataDraft = null;
    return;
  }

  state.selection =
    snapshot.containers.length > 0 ? (`container:${snapshot.containers[0]!.vmid}` as ViewSelection) : "settings";
  syncContainerMetadataDraft(snapshot);
}

function escapeHtml(value: unknown): string {
  return normalizeString(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function icon(name: IconName): string {
  const paths: Record<IconName, string> = {
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
  const active = current === selection ? " active" : "";
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

  if (container.dropins.length > 0 && parts.length < 2) {
    parts.push(`${container.dropins.length} drop-in${container.dropins.length === 1 ? "" : "s"}`);
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
    state.expandedGroups = nextIds;
    state.expandedGroupsInitialized = true;
    return;
  }

  for (const id of [...state.expandedGroups]) {
    if (!nextIds.has(id)) {
      state.expandedGroups.delete(id);
    }
  }

  for (const id of nextIds) {
    if (!state.expandedGroups.has(id)) {
      state.expandedGroups.add(id);
    }
  }
}

function renderSidebar(snapshot: AppSnapshot): string {
  const containerGroups = sidebarGroups(snapshot);
  const containerButtons =
    containerGroups.length > 0
      ? containerGroups
          .map((group) => {
            const headingClass = group.isPrimary ? "nav-group-heading primary" : "nav-group-heading";
            const items = group.containers
              .map((container) => {
                const selection = `container:${container.vmid}` as ViewSelection;
                const active = state.selection === selection ? " active" : "";
                const statusClass = container.secretGroups.length > 0 || container.hasIdentity ? "ok" : "info";
                const badges = [
                  container.hasIdentity ? `<span class="nav-badge" title="Has age identity">K</span>` : "",
                  container.secretGroups.length > 0
                    ? `<span class="nav-badge" title="${escapeHtml(container.secretGroups.join(", "))}">${container.secretGroups.length}</span>`
                    : "",
                ]
                  .filter(Boolean)
                  .join("");
                const detail = sidebarContainerDetail(snapshot, container);

                return `
                  <button class="nav-item${active}" data-nav="${selection}" title="${escapeHtml(`VMID ${container.vmid}`)}">
                    <span class="tiny-dot ${statusClass}" aria-hidden="true"></span>
                    <span class="nav-copy">
                      <span class="nav-item-title">${escapeHtml(sidebarTitleFor(snapshot, container))}</span>
                      ${
                        detail
                          ? `<span class="nav-item-detail">${escapeHtml(detail)}</span>`
                          : ""
                      }
                    </span>
                    <span class="nav-meta">${badges}</span>
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
        <div class="brand-mark">${icon("spark")}</div>
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
          ${renderNavItem("branch", "Git", "git", state.selection, "")}
          ${renderNavItem("health", "Doctor", "doctor", state.selection, "")}
          ${renderNavItem("publish", "Publish", "publish", state.selection, "")}
          ${renderNavItem("lock", "Secrets", "secrets", state.selection, "")}
        </div>
      </section>

      <section class="nav-section">
        <div class="nav-heading">
          <span>Containers</span>
          <span class="nav-count">${snapshot.containers.length}</span>
        </div>
        <div class="nav-list nav-list-groups">${containerButtons}</div>
      </section>

      <section class="nav-section">
        <div class="nav-heading">
          <span>App</span>
        </div>
        <div class="nav-list">
          ${renderNavItem("gear", "Settings", "settings", state.selection, "")}
        </div>
      </section>

      <div class="sidebar-footer">
        <div class="sidebar-footer-copy">
          ${escapeHtml(snapshot.config.secretProvider)} &bull; ${snapshot.containers.length} container${snapshot.containers.length === 1 ? "" : "s"}
        </div>
      </div>
    </aside>
  `;
}

function renderStatTile(
  iconName: IconName,
  tone: "accent" | "blue" | "amber" | "magenta",
  value: string,
  label: string,
): string {
  return `
    <div class="stat-tile">
      <div class="stat-icon ${tone}">${icon(iconName)}</div>
      <div>
        <div class="stat-value">${escapeHtml(value)}</div>
        <div class="stat-label">${escapeHtml(label)}</div>
      </div>
    </div>
  `;
}

function renderSummary(snapshot: AppSnapshot): string {
  const containersWithGroups = snapshot.containers.filter((container) => container.secretGroups.length > 0).length;
  const withIdentity = snapshot.containers.filter((container) => container.hasIdentity).length;
  const dropins = snapshot.containers.reduce((sum, container) => sum + container.dropins.length, 0);

  return `
    <section class="summary-band">
      ${renderStatTile("box", "accent", String(snapshot.containers.length), "Containers")}
      ${renderStatTile("lock", "blue", String(containersWithGroups), "With secrets")}
      ${renderStatTile("key", "amber", String(withIdentity), "With identity")}
      ${renderStatTile("spark", "magenta", String(dropins), "Drop-ins")}
    </section>
  `;
}

function renderWarnings(snapshot: AppSnapshot): string {
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

  const title =
    (currentContainer && state.snapshot ? sidebarTitleFor(state.snapshot, currentContainer) : null) ??
    (state.selection === "settings"
      ? "Settings"
      : state.selection === "publish"
        ? "Publish"
        : state.selection === "secrets"
          ? "Secrets"
          : state.selection === "doctor"
            ? "Doctor"
            : state.selection === "git"
              ? "Git"
              : "Proxnix Manager");

  const subtitleMap: Record<string, string> = {
    settings: "Paths, SSH targets, and secret backend used across all proxnix tools.",
    publish: "Sync config, secrets, and identities to your Proxmox hosts.",
    secrets: "Secret groups and which containers use them.",
    doctor: "Check your site for misconfigurations and missing files.",
    git: "Current branch, uncommitted changes, and recent history.",
  };

  const subtitle = currentContainer
    ? "Config files, secret groups, and identity for this container."
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

function renderMetaCard(label: string, value: string): string {
  return `
    <div class="meta-card">
      <div class="meta-label">${escapeHtml(label)}</div>
      <div class="meta-value"><code>${escapeHtml(value)}</code></div>
    </div>
  `;
}

function renderOnboarding(snapshot: AppSnapshot): string {
  return `
    <div class="page-stack">
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Getting started</div>
          <div class="hero-title">Point to your site repo</div>
          <div class="hero-text">
            Select the root of your proxnix site directory. The app will
            discover containers, secrets, and identities from there.
          </div>
        </div>
        <div class="hero-actions">
          <button class="primary-button" data-action="choose-site">
            ${icon("folder")}
            <span>Choose Site Directory</span>
          </button>
          <button class="secondary-button" data-nav="settings">
            ${icon("gear")}
            <span>Review Settings</span>
          </button>
        </div>
      </section>
      ${renderSettingsForm(snapshot)}
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
      </div>

      <div class="meta-grid">
        ${renderMetaCard("Config path", snapshot.configPath)}
        ${renderMetaCard("Current site dir", snapshot.config.siteDir || "(unset)")}
      </div>
    </section>
  `;
}

function syncDraftIndicators(): void {
  const dirty = isDirty();
  const saveButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="save-config"]');
  const resetButtons = root.querySelectorAll<HTMLButtonElement>('[data-action="reset-draft"]');
  const status = root.querySelector<HTMLDivElement>(".toolbar-status");
  const statusLabel = status?.querySelector("span") ?? null;

  for (const button of saveButtons) {
    button.disabled = !dirty || state.saving;
  }

  for (const button of resetButtons) {
    button.disabled = !dirty || state.saving;
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
  const hasMetadata =
    (state.containerMetadataDraft?.displayName ?? "").length > 0 ||
    (state.containerMetadataDraft?.group ?? "").length > 0 ||
    (state.containerMetadataDraft?.labels.length ?? 0) > 0;

  for (const button of saveButtons) {
    button.disabled = !dirty || state.metadataSaving;
  }
  for (const button of resetButtons) {
    button.disabled = !dirty || state.metadataSaving;
  }
  for (const button of clearButtons) {
    button.disabled = !hasMetadata || state.metadataSaving;
  }
}

function renderSidebarMetadataForm(container: ContainerSummary, snapshot: AppSnapshot): string {
  const metadata = state.containerMetadataDraft ?? currentContainerSidebarMetadata(snapshot, container.vmid);
  const dirty = sidebarMetadataDirty(container, snapshot);
  const hasMetadata = metadata.displayName || metadata.group || metadata.labels.length > 0;

  return `
    <section class="page-band">
      <div class="section-header">
        <div>
          <div class="section-title">${icon("spark")}<span>Display Settings</span></div>
          <div class="section-copy">
            Custom name, group, and labels for this container in the sidebar.
          </div>
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

      <div class="pill-row">
        ${pill(metadata.displayName ? `Alias: ${metadata.displayName}` : `VMID ${container.vmid}`, metadata.displayName ? "good" : "info", metadata.displayName ? "spark" : "box")}
        ${pill(metadata.group || "No custom group", metadata.group ? "magenta" : "info", "folder")}
        ${pill(
          metadata.labels.length > 0 ? `${metadata.labels.length} label(s)` : "No labels",
          metadata.labels.length > 0 ? "good" : "info",
          "key",
        )}
      </div>

      <div class="form-grid">
        ${renderSettingsField("Display name", "displayName", metadata.displayName, "Friendly name shown instead of the VMID.", undefined, true, false, "data-container-field")}
        ${renderSettingsField("Group", "group", metadata.group, "Sidebar group heading.", undefined, false, false, "data-container-field")}
        ${renderSettingsField("Labels", "labels", metadata.labels.join(", "), "Comma-separated tags shown in the sidebar.", undefined, true, false, "data-container-field")}
      </div>
    </section>
  `;
}

function renderContainerPage(container: ContainerSummary): string {
  const snapshot = state.snapshot;
  const metadata = snapshot ? sidebarMetadataFor(snapshot, container.vmid) : defaultSidebarMetadata();
  const title = metadata.displayName || `VMID ${container.vmid}`;
  const subtitle = metadata.displayName ? `VMID ${container.vmid}` : "Container workspace";
  const isEmbeddedSops = usesEmbeddedSops(snapshot?.config.secretProvider ?? "");
  const labels =
    metadata.labels.length > 0
      ? `<div class="pill-row">${metadata.labels
          .map((label) => pill(label, "info", "spark"))
          .join("")}</div>`
      : "";

  return `
    <div class="page-stack">
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">${escapeHtml(subtitle)}</div>
          <div class="hero-title">${escapeHtml(title)}</div>
          <div class="hero-text">
            Config, secrets, identity, and drop-in overlays for this container.
          </div>
        </div>

        <div class="pill-row">
          ${pill(container.hasConfig ? "Config found" : "No config dir", container.hasConfig ? "good" : "warn", "box")}
          ${pill(`${container.secretGroups.length} secret group${container.secretGroups.length === 1 ? "" : "s"}`, container.secretGroups.length > 0 ? "magenta" : "info", "lock")}
          ${pill(container.hasIdentity ? "Identity present" : "No identity", container.hasIdentity ? "good" : "warn", "key")}
          ${pill(`Secrets via ${snapshot?.config.secretProvider ?? "unknown"}`, "info", "spark")}
        </div>

        ${labels}

        <div class="action-row">
          <button class="secondary-button" data-action="open-in-editor" data-path="${escapeHtml(container.containerPath)}">
            ${icon("edit")}
            <span>Open in Editor</span>
          </button>
          <button class="secondary-button" data-action="open-path" data-path="${escapeHtml(container.containerPath)}">
            ${icon("folder")}
            <span>Open Container Dir</span>
          </button>
          ${isEmbeddedSops ? `
            <button class="secondary-button" data-action="open-path" data-path="${escapeHtml(container.privateContainerPath)}">
              ${icon("folder")}
              <span>Open Private Dir</span>
            </button>
          ` : ""}
        </div>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("spark")}<span>Paths</span></div>
            <div class="section-copy">
              Resolved from site directory and VMID.
            </div>
          </div>
        </div>
        <div class="meta-grid">
          ${renderMetaCard("Container path", container.containerPath)}
          ${isEmbeddedSops ? renderMetaCard("Private path", container.privateContainerPath) : ""}
        </div>
      </section>

      ${snapshot ? renderSidebarMetadataForm(container, snapshot) : ""}

      <section class="page-band">
        <div class="details-grid">
          <div class="list-block">
            <div class="section-title">${icon("box")}<span>Drop-ins</span></div>
            <div class="list">
              ${
                container.dropins.length > 0
                  ? container.dropins
                      .map(
                        (dropin) => `
                          <div class="list-item">
                            <div class="list-item-copy">
                              <div class="list-item-title"><code>${escapeHtml(dropin)}</code></div>
                              <div class="list-item-meta">repo overlay</div>
                            </div>
                          </div>
                        `,
                      )
                      .join("")
                  : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No drop-ins configured.</div></div></div>`
              }
            </div>
          </div>

          <div class="list-block">
            <div class="section-title">${icon("lock")}<span>Secret Groups</span></div>
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
                          </div>
                        `,
                      )
                      .join("")
                  : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No secret groups attached.</div></div></div>`
              }
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function fileStatusClass(status: string): string {
  if (status.includes("M")) return "modified";
  if (status.includes("A")) return "added";
  if (status.includes("D")) return "deleted";
  if (status.includes("R")) return "renamed";
  if (status.includes("?")) return "untracked";
  return "modified";
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
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Diagnostics</div>
          <div class="hero-title">Health Check</div>
          <div class="hero-text">
            Checks site structure, secrets, and publish trees for problems.
          </div>
        </div>
        <div class="option-row">
          <label class="option-toggle">
            <input type="checkbox" data-option="doctorConfigOnly" ${state.doctorConfigOnly ? "checked" : ""} />
            <span>Config only</span>
          </label>
          <div class="option-field">
            <span class="option-field-label">Target VMID</span>
            <input type="text" data-option="doctorVmid" value="${escapeHtml(state.doctorVmid)}" placeholder="All" spellcheck="false" />
          </div>
        </div>
        <div class="hero-actions">
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
  const hasHosts = snapshot.config.hosts.trim().length > 0;

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
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Deployment</div>
          <div class="hero-title">Publish</div>
          <div class="hero-text">
            Compiles and pushes config, secrets, and identities to your target hosts over SSH.
          </div>
        </div>
        <div class="pill-row">
          ${pill(hasHosts ? `Hosts: ${snapshot.config.hosts}` : "No hosts configured", hasHosts ? "info" : "warn", "spark")}
          ${pill(snapshot.config.secretProvider, "magenta", "lock")}
        </div>
        <div class="option-row">
          <label class="option-toggle">
            <input type="checkbox" data-option="publishConfigOnly" ${state.publishConfigOnly ? "checked" : ""} />
            <span>Config only</span>
          </label>
          <div class="option-field">
            <span class="option-field-label">Target VMID</span>
            <input type="text" data-option="publishVmid" value="${escapeHtml(state.publishVmid)}" placeholder="All" spellcheck="false" />
          </div>
        </div>
        <div class="hero-actions">
          <button class="secondary-button" data-action="publish-preview" ${running || !hasHosts ? "disabled" : ""}>
            ${icon("refresh")}
            <span>Preview Changes</span>
          </button>
          <button class="primary-button" data-action="publish-execute" ${running || !hasHosts ? "disabled" : ""}>
            ${icon("publish")}
            <span>${running ? "Publishing..." : "Publish Now"}</span>
          </button>
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
  const loading = state.gitLoading;

  if (loading && !result) {
    return `
      <div class="page-stack">
        <section class="hero-band">
          <div class="running-band">${icon("refresh")}<span>Loading repository status...</span></div>
        </section>
      </div>
    `;
  }

  if (!result || result.error) {
    return `
      <div class="page-stack">
        <section class="hero-band">
          <div class="hero-copy">
            <div class="eyebrow">Version control</div>
            <div class="hero-title">Repository</div>
            <div class="hero-text">
              ${result?.error ? escapeHtml(result.error) : "Could not load repository status."}
            </div>
          </div>
          <div class="hero-actions">
            <button class="secondary-button" data-action="refresh-git" ${loading ? "disabled" : ""}>
              ${icon("refresh")}
              <span>Retry</span>
            </button>
          </div>
        </section>
      </div>
    `;
  }

  const filesHtml =
    result.files.length > 0
      ? result.files
          .map(
            (file) => `
              <div class="list-item">
                <div class="list-item-copy" style="flex-direction:row;align-items:center;gap:10px;">
                  <span class="file-status-code ${fileStatusClass(file.status)}">${escapeHtml(file.status)}</span>
                  <code class="list-item-title">${escapeHtml(file.path)}</code>
                </div>
              </div>
            `,
          )
          .join("")
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
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Version control</div>
          <div class="hero-title">Repository</div>
          <div class="hero-text">
            Uncommitted changes and recent commits in the site repo.
          </div>
        </div>
        <div class="pill-row">
          ${pill(result.branch ? `Branch: ${result.branch}` : "Detached HEAD", "info", "branch")}
          ${pill(result.clean ? "Clean" : `${result.files.length} changed file${result.files.length === 1 ? "" : "s"}`, result.clean ? "good" : "warn", result.clean ? "spark" : "refresh")}
        </div>
        <div class="hero-actions">
          <button class="secondary-button" data-action="refresh-git" ${loading ? "disabled" : ""}>
            ${icon("refresh")}
            <span>${loading ? "Loading..." : "Refresh"}</span>
          </button>
          <button class="secondary-button" data-action="open-in-editor" data-path="${escapeHtml(snapshot.config.siteDir)}">
            ${icon("edit")}
            <span>Open in Editor</span>
          </button>
          <button class="secondary-button" data-action="open-site">
            ${icon("folder")}
            <span>Open in Finder</span>
          </button>
        </div>
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

function renderSecretsPage(snapshot: AppSnapshot): string {
  const defined = new Set(snapshot.definedSecretGroups);
  const attached = new Set(snapshot.attachedSecretGroups);
  const allGroups = [...new Set([...defined, ...attached])].sort();

  const groupContainers = new Map<string, ContainerSummary[]>();
  for (const container of snapshot.containers) {
    for (const group of container.secretGroups) {
      const list = groupContainers.get(group) ?? [];
      list.push(container);
      groupContainers.set(group, list);
    }
  }

  const groupCards =
    allGroups.length > 0
      ? allGroups
          .map((group) => {
            const isDefined = defined.has(group);
            const containers = groupContainers.get(group) ?? [];
            const statusPill = isDefined
              ? pill("Configured", "good", "spark")
              : pill("Referenced only", "warn", "health");
            const containerTags =
              containers.length > 0
                ? `<div class="group-container-tags">
                    ${containers
                      .map((container) => {
                        const title = sidebarTitleFor(snapshot, container);
                        return `<span class="group-container-tag">${escapeHtml(title)}</span>`;
                      })
                      .join("")}
                  </div>`
                : `<div class="group-card-meta">Not used by any container.</div>`;

            return `
              <div class="group-card">
                <div class="group-card-title"><code>${escapeHtml(group)}</code></div>
                <div class="pill-row">${statusPill}</div>
                ${containerTags}
              </div>
            `;
          })
          .join("")
      : "";

  const orphanedAttached = [...attached].filter((g) => !defined.has(g));
  const unusedDefined = [...defined].filter((g) => !attached.has(g));

  return `
    <div class="page-stack">
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Secret management</div>
          <div class="hero-title">Secret Groups</div>
          <div class="hero-text">
            Containers declare group membership in <code>secret-groups.list</code>.
            Secrets are managed by ${escapeHtml(snapshot.config.secretProvider)}.
          </div>
        </div>
        <div class="pill-row">
          ${pill(snapshot.config.secretProvider, "info", "spark")}
          ${pill(`${snapshot.definedSecretGroups.length} defined`, "magenta", "lock")}
          ${pill(`${snapshot.attachedSecretGroups.length} in use`, "good", "key")}
          ${orphanedAttached.length > 0 ? pill(`${orphanedAttached.length} missing`, "warn", "health") : ""}
          ${unusedDefined.length > 0 ? pill(`${unusedDefined.length} unused`, "info", "box") : ""}
        </div>
      </section>

      ${
        allGroups.length > 0
          ? `
            <section class="page-band">
              <div class="section-header">
                <div>
                  <div class="section-title">${icon("lock")}<span>All Groups</span></div>
                  <div class="section-copy">
                    Groups referenced by containers and their status in ${escapeHtml(snapshot.config.secretProvider)}.
                  </div>
                </div>
              </div>
              <div class="group-grid">${groupCards}</div>
            </section>
          `
          : `
            <section class="page-band">
              <div class="empty-state">
                No secret groups yet. Add group names to a container's
                <code>secret-groups.list</code> and set them up in ${escapeHtml(snapshot.config.secretProvider)}.
              </div>
            </section>
          `
      }

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("box")}<span>Per Container</span></div>
            <div class="section-copy">
              Group membership and identity status for each container.
            </div>
          </div>
        </div>
        <div class="list">
          ${
            snapshot.containers.length > 0
              ? snapshot.containers
                  .map((container) => {
                    const title = sidebarTitleFor(snapshot, container);
                    const groups = container.secretGroups;
                    return `
                      <div class="list-item">
                        <div class="list-item-copy">
                          <div class="list-item-title">${escapeHtml(title)}</div>
                          <div class="list-item-meta">
                            ${groups.length > 0 ? `Groups: ${groups.map((g) => escapeHtml(g)).join(", ")}` : "No groups attached"}
                            ${container.hasIdentity ? " &bull; Identity present" : ""}
                          </div>
                        </div>
                        <div class="nav-meta">
                          ${container.hasIdentity ? `<span class="nav-badge" title="Has age identity">K</span>` : ""}
                          ${groups.length > 0 ? `<span class="nav-badge" title="${escapeHtml(groups.join(", "))}">${groups.length}</span>` : ""}
                        </div>
                      </div>
                    `;
                  })
                  .join("")
              : `<div class="list-item"><div class="list-item-copy"><div class="list-item-title">No containers found.</div></div></div>`
          }
        </div>
      </section>
    </div>
  `;
}

function renderMain(snapshot: AppSnapshot): string {
  if (snapshot.config.siteDir.length === 0) {
    return renderOnboarding(snapshot);
  }

  if (state.selection === "settings") {
    return renderSettingsForm(snapshot);
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

  if (state.selection === "doctor") {
    return renderDoctorPage(snapshot);
  }

  return renderGitPage(snapshot);
}

function renderStatusbar(snapshot: AppSnapshot): string {
  return `
    <footer class="statusbar">
      <div class="statusbar-meta">
        <span>Config: <code>${escapeHtml(snapshot.configPath)}</code></span>
        <span>Secrets: <code>${escapeHtml(snapshot.config.secretProvider)}</code></span>
        <span>Groups: <code>${snapshot.definedSecretGroups.length}</code> configured / <code>${snapshot.attachedSecretGroups.length}</code> referenced</span>
      </div>
      <div class="statusbar-meta">
        <span>Bridge: embedded python script</span>
      </div>
    </footer>
  `;
}

function render(): void {
  if (state.loading && !state.snapshot) {
    root.innerHTML = `<div class="loading-state">Loading proxnix workstation state...</div>`;
    return;
  }

  const snapshot = state.snapshot;
  if (!snapshot) {
    root.innerHTML = `<div class="loading-state">No proxnix state available.</div>`;
    return;
  }

  root.innerHTML = `
    <div class="shell">
      ${renderSidebar(snapshot)}
      <main class="main">
        ${renderToolbar(snapshot)}
        ${renderSummary(snapshot)}
        ${renderWarnings(snapshot)}
        <div class="page-scroll">
          ${renderMain(snapshot)}
        </div>
        ${renderStatusbar(snapshot)}
      </main>
    </div>
  `;
  syncDraftIndicators();
  syncSidebarMetadataIndicators();
}

async function refreshSnapshot(): Promise<void> {
  state.loading = true;
  state.error = null;
  render();

  try {
    const snapshot = normalizeSnapshot(await proxnixRpc.request.loadSnapshot());
    syncExpandedGroups(snapshot);
    state.snapshot = snapshot;
    state.draft = cloneConfig(snapshot.config);
    ensureSelection(snapshot);
    syncContainerMetadataDraft(snapshot);
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
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

async function handleAction(action: string, element: HTMLElement): Promise<void> {
  if (!state.snapshot) {
    return;
  }

  if (action === "refresh") {
    await refreshSnapshot();
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

  if (action === "refresh-git") {
    await handleRefreshGit();
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
      syncExpandedGroups(snapshot);
      state.snapshot = snapshot;
      state.draft = cloneConfig(snapshot.config);
      ensureSelection(snapshot);
      syncContainerMetadataDraft(snapshot);
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
    render();
    return;
  }

  if (action === "clear-sidebar-metadata") {
    state.containerMetadataDraft = defaultSidebarMetadata();
    render();
    return;
  }

  if (action === "save-sidebar-metadata") {
    const container = selectedContainer();
    if (!container || !state.containerMetadataDraft || !state.snapshot) {
      return;
    }

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
      syncExpandedGroups(snapshot);
      state.snapshot = snapshot;
      state.draft = cloneConfig(snapshot.config);
      ensureSelection(snapshot);
      syncContainerMetadataDraft(snapshot);
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error);
    } finally {
      state.metadataSaving = false;
      render();
    }
  }
}

root.addEventListener("click", (event) => {
  const target = event.target as HTMLElement | null;
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

function updateOptionFromField(target: HTMLInputElement): void {
  const option = target.dataset.option;
  if (!option) return;

  if (target.type === "checkbox") {
    if (option === "doctorConfigOnly") state.doctorConfigOnly = target.checked;
    if (option === "publishConfigOnly") state.publishConfigOnly = target.checked;
  } else {
    if (option === "doctorVmid") state.doctorVmid = target.value;
    if (option === "publishVmid") state.publishVmid = target.value;
  }
}

root.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    if (target.dataset.option) {
      updateOptionFromField(target);
    } else if (target.dataset.containerField) {
      updateContainerMetadataFromField(target);
    } else {
      updateDraftFromField(target);
    }
  }
});

root.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement) {
    updateDraftFromField(target);
  } else if (target instanceof HTMLInputElement && target.type === "checkbox" && target.dataset.option) {
    updateOptionFromField(target);
  }
});

void refreshSnapshot();
