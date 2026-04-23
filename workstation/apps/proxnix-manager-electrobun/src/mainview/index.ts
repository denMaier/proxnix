import { Electroview } from "electrobun/view";
import type {
  AppSnapshot,
  ContainerSummary,
  ProxnixConfig,
  ProxnixManagerRPC,
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
  selection: ViewSelection;
  loading: boolean;
  saving: boolean;
  error: string | null;
} = {
  snapshot: null,
  draft: null,
  selection: "settings",
  loading: true,
  saving: false,
  error: null,
};

function defaultConfig(): ProxnixConfig {
  return {
    siteDir: "",
    sopsMasterIdentity: "~/.ssh/id_ed25519",
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

function setSelection(next: ViewSelection): void {
  state.selection = next;
  render();
}

function ensureSelection(snapshot: AppSnapshot): void {
  if (snapshot.config.siteDir.length === 0) {
    state.selection = "settings";
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
    return;
  }

  state.selection =
    snapshot.containers.length > 0 ? (`container:${snapshot.containers[0]!.vmid}` as ViewSelection) : "settings";
}

function escapeHtml(value: string): string {
  return value
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

function renderSidebar(snapshot: AppSnapshot): string {
  const containerButtons =
    snapshot.containers.length > 0
      ? snapshot.containers
          .map((container) => {
            const selection = `container:${container.vmid}` as ViewSelection;
            const active = state.selection === selection ? " active" : "";
            const statusClass = container.hasSecretStore ? "ok" : "info";
            const badges = [
              container.hasIdentity ? `<span class="nav-badge" title="Has age identity">K</span>` : "",
              container.secretGroups.length > 0
                ? `<span class="nav-badge" title="${escapeHtml(container.secretGroups.join(", "))}">${container.secretGroups.length}</span>`
                : "",
            ]
              .filter(Boolean)
              .join("");

            return `
              <button class="nav-item${active}" data-nav="${selection}">
                <span class="tiny-dot ${statusClass}" aria-hidden="true"></span>
                <span class="nav-item-title">${escapeHtml(container.vmid)}</span>
                <span class="nav-meta">${badges}</span>
              </button>
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
          <div class="eyebrow">Electrobun Preview</div>
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
          ${renderNavItem("publish", "Publish All", "publish", state.selection, "")}
          ${renderNavItem("lock", "Secrets", "secrets", state.selection, "")}
        </div>
      </section>

      <section class="nav-section">
        <div class="nav-heading">
          <span>Containers</span>
          <span class="nav-count">${snapshot.containers.length}</span>
        </div>
        <div class="nav-list">${containerButtons}</div>
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
        <div class="sidebar-footer-title">Current migration slice</div>
        <div class="sidebar-footer-copy">
          Settings and container discovery are wired. Publish, Secrets, Doctor,
          and Git still need their command execution screens.
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
  const withSecrets = snapshot.containers.filter((container) => container.hasSecretStore).length;
  const withIdentity = snapshot.containers.filter((container) => container.hasIdentity).length;
  const dropins = snapshot.containers.reduce((sum, container) => sum + container.dropins.length, 0);

  return `
    <section class="summary-band">
      ${renderStatTile("box", "accent", String(snapshot.containers.length), "Containers")}
      ${renderStatTile("lock", "blue", String(withSecrets), "Secret stores")}
      ${renderStatTile("key", "amber", String(withIdentity), "Age identities")}
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
    currentContainer?.vmid ??
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

  const subtitle = currentContainer
    ? "Inspect the local container tree, attached secret groups, and generated sidecar state."
    : state.selection === "settings"
      ? "Point the app at your proxnix site repo and keep workstation config aligned with the Python backend."
      : "This screen is scaffolded and ready for command execution wiring in the next migration slice.";

  const canOpenSite = snapshot.config.siteDir.length > 0;

  return `
    <header class="toolbar">
      <div class="toolbar-copy">
        <div class="eyebrow">Cross-platform workstation app</div>
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
          <div class="eyebrow">First usable slice</div>
          <div class="hero-title">Point Proxnix Manager at a site repo.</div>
          <div class="hero-text">
            This Electrobun app already reads and writes the proxnix workstation config,
            preserves unknown PROXNIX_* assignments, and scans the repo for containers,
            groups, identities, and secret stores.
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
  field: keyof ProxnixConfig,
  value: string,
  hint: string,
  options?: string[],
  wide = false,
  browse = false,
): string {
  const control = options
    ? `
        <div class="field-control">
          <select data-field="${field}">
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
            <input data-field="${field}" value="${escapeHtml(value)}" spellcheck="false" />
            <button class="secondary-button" data-action="choose-site" title="Choose site directory">
              ${icon("folder")}
              <span>Browse</span>
            </button>
          </div>
        `
      : `
          <div class="field-control">
            <input data-field="${field}" value="${escapeHtml(value)}" spellcheck="false" />
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
      ? `${snapshot.preservedConfigKeys.length} extra PROXNIX_* assignment(s) will be preserved on save.`
      : "Unknown PROXNIX_* assignments are preserved when the config is saved.";

  return `
    <section class="page-band">
      <div class="section-header">
        <div>
          <div class="section-title">${icon("gear")}<span>Workstation Settings</span></div>
          <div class="section-copy">
            Edit the same config file used by the workstation CLI and TUI. This form writes
            PROXNIX_SOPS_MASTER_IDENTITY instead of the older PROXNIX_MASTER_IDENTITY key.
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
        ${pill(snapshot.configExists ? "Config file found" : "Config file will be created", snapshot.configExists ? "good" : "warn", snapshot.configExists ? "spark" : "gear")}
        ${pill(dirty ? "Draft differs from disk" : "Draft matches disk", dirty ? "warn" : "info", dirty ? "refresh" : "spark")}
        ${pill(preserved, "magenta", "lock")}
      </div>

      <div class="form-grid">
        ${renderSettingsField("Site directory", "siteDir", draft.siteDir, "Repo root that contains containers/ and private/.", undefined, true, true)}
        ${renderSettingsField("SOPS master identity", "sopsMasterIdentity", draft.sopsMasterIdentity, "SSH private key used by embedded-sops.", undefined, true)}
        ${renderSettingsField("SSH hosts", "hosts", draft.hosts, "Space-separated publish targets like root@node1 root@node2.")}
        ${renderSettingsField("SSH identity", "sshIdentity", draft.sshIdentity, "Optional override for ssh -i. Leave blank to use your agent.")}
        ${renderSettingsField("Remote dir", "remoteDir", draft.remoteDir, "Public relay root on target hosts.")}
        ${renderSettingsField("Remote private dir", "remotePrivDir", draft.remotePrivDir, "Private relay root on target hosts.")}
        ${renderSettingsField("Host relay identity", "remoteHostRelayIdentity", draft.remoteHostRelayIdentity, "Relay identity path on target hosts.")}
        ${renderSettingsField("Secret provider", "secretProvider", draft.secretProvider, "Backend used for source secret retrieval.", SECRET_PROVIDER_OPTIONS)}
        ${renderSettingsField("Provider command", "secretProviderCommand", draft.secretProviderCommand, "Command used when the provider is set to exec.", undefined, true)}
        ${renderSettingsField("Scripts dir", "scriptsDir", draft.scriptsDir, "Optional override for proxnix command wrappers.", undefined, true)}
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

function renderContainerPage(container: ContainerSummary): string {
  return `
    <div class="page-stack">
      <section class="hero-band">
        <div class="hero-copy">
          <div class="eyebrow">Container workspace</div>
          <div class="hero-title">VMID ${escapeHtml(container.vmid)}</div>
          <div class="hero-text">
            This first Electrobun pass mirrors the Swift app's local inspection layer:
            repo discovery, secret-group parsing, and sidecar state checks.
          </div>
        </div>

        <div class="pill-row">
          ${pill(container.hasConfig ? "Config dir present" : "Config dir missing", container.hasConfig ? "good" : "warn", "box")}
          ${pill(container.hasSecretStore ? "Secret store ready" : "No secret store", container.hasSecretStore ? "good" : "info", "lock")}
          ${pill(container.hasIdentity ? "Age identity present" : "Identity missing", container.hasIdentity ? "good" : "warn", "key")}
          ${pill(`${container.secretGroups.length} secret group(s)`, "magenta", "spark")}
        </div>

        <div class="action-row">
          <button class="secondary-button" data-action="open-path" data-path="${escapeHtml(container.containerPath)}">
            ${icon("folder")}
            <span>Open Container Dir</span>
          </button>
          <button class="secondary-button" data-action="open-path" data-path="${escapeHtml(container.privateContainerPath)}">
            ${icon("folder")}
            <span>Open Private Dir</span>
          </button>
        </div>
      </section>

      <section class="page-band">
        <div class="section-header">
          <div>
            <div class="section-title">${icon("spark")}<span>Container State</span></div>
            <div class="section-copy">
              These values come from the repo itself, not from a generated cache.
            </div>
          </div>
        </div>
        <div class="meta-grid">
          ${renderMetaCard("Public path", container.containerPath)}
          ${renderMetaCard("Private path", container.privateContainerPath)}
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

function renderActionPlaceholder(
  iconName: IconName,
  title: string,
  copy: string,
  nextSlice: string,
): string {
  return `
    <div class="page-stack">
      <section class="hero-band">
        <div class="action-placeholder">
          <div class="action-placeholder-mark">${icon(iconName)}</div>
          <div class="hero-copy">
            <div class="eyebrow">Scaffolded screen</div>
            <div class="hero-title">${escapeHtml(title)}</div>
            <div class="hero-text">${escapeHtml(copy)}</div>
            <div class="pill-row">
              ${pill(nextSlice, "info", "refresh")}
            </div>
          </div>
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
      return renderActionPlaceholder(
        "box",
        "Container not found",
        "The selected VMID is no longer present in the current site scan.",
        "Refresh the site state and pick a different container.",
      );
    }
    return renderContainerPage(container);
  }

  if (state.selection === "publish") {
    return renderActionPlaceholder(
      "publish",
      "Publish workflow",
      "The next slice will stream proxnix publish output through Bun RPC and reuse the existing workstation command line flags.",
      "Wire proxnix-publish and its progress log.",
    );
  }

  if (state.selection === "secrets") {
    return renderActionPlaceholder(
      "lock",
      "Secrets workflow",
      "The next slice will bridge secret listing, rotation, and group attachment on top of the existing workstation secret provider layer.",
      "Wire proxnix-secrets and scoped group operations.",
    );
  }

  if (state.selection === "doctor") {
    return renderActionPlaceholder(
      "health",
      "Doctor workflow",
      "The next slice will stream the workstation doctor report and preserve its warning and failure structure in the desktop UI.",
      "Wire proxnix-doctor with structured output.",
    );
  }

  return renderActionPlaceholder(
    "branch",
    "Git workflow",
    "The next slice will add site repository status, diff summary, commit, and push operations inside the Electrobun shell.",
    "Wire git status and commit actions.",
  );
}

function renderStatusbar(snapshot: AppSnapshot): string {
  return `
    <footer class="statusbar">
      <div class="statusbar-meta">
        <span>Config: <code>${escapeHtml(snapshot.configPath)}</code></span>
        <span>Provider: <code>${escapeHtml(snapshot.config.secretProvider)}</code></span>
        <span>Groups: <code>${snapshot.definedSecretGroups.length}</code> defined / <code>${snapshot.attachedSecretGroups.length}</code> attached</span>
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
}

async function refreshSnapshot(): Promise<void> {
  state.loading = true;
  state.error = null;
  render();

  try {
    const snapshot = await proxnixRpc.request.loadSnapshot();
    state.snapshot = snapshot;
    state.draft = cloneConfig(snapshot.config);
    ensureSelection(snapshot);
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error);
  } finally {
    state.loading = false;
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
      const snapshot = await proxnixRpc.request.saveConfig({ config: state.draft });
      state.snapshot = snapshot;
      state.draft = cloneConfig(snapshot.config);
      ensureSelection(snapshot);
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error);
    } finally {
      state.saving = false;
      render();
    }
  }
}

root.addEventListener("click", (event) => {
  const target = event.target as HTMLElement | null;
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

root.addEventListener("input", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement) {
    updateDraftFromField(target);
  }
});

root.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement) {
    updateDraftFromField(target);
  }
});

void refreshSnapshot();
