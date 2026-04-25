import type { RPCSchema } from "electrobun/bun";
import type { ProxmoxNodesResult } from "./proxmoxTypes";

export interface ProxnixConfig {
  siteDir: string;
  sopsMasterIdentity: string;
  hosts: string;
  sshIdentity: string;
  remoteDir: string;
  remotePrivDir: string;
  remoteHostRelayIdentity: string;
  secretProvider: string;
  secretProviderCommand: string;
  scriptsDir: string;
  managerPythonPath: string;
  proxmoxApiEnabled: string;
  proxmoxApiUrl: string;
  proxmoxApiTokenId: string;
  proxmoxApiTokenSecret: string;
  proxmoxVerifyTls: string;
}

export interface ContainerSummary {
  vmid: string;
  containerPath: string;
  privateContainerPath: string;
  dropins: string[];
  hasConfig: boolean;
  hasIdentity: boolean;
  secretGroups: string[];
}

export interface SidebarMetadata {
  displayName: string;
  group: string;
  labels: string[];
}

export interface DoctorEntry {
  level: string;
  text: string;
}

export interface DoctorSection {
  heading: string;
  entries: DoctorEntry[];
}

export interface DoctorResult {
  sections: DoctorSection[];
  oks: number;
  warns: number;
  fails: number;
  exitCode?: number;
  error?: string;
}

export interface CommandResult {
  output: string;
  exitCode: number;
  error?: string;
}

export interface OnboardingResult {
  snapshot: AppSnapshot;
  output: string;
  actions: string[];
}

export interface GitFile {
  status: string;
  path: string;
}

export interface GitLogEntry {
  hash: string;
  message: string;
}

export interface GitStatusResult {
  isRepo?: boolean;
  branch: string;
  clean: boolean;
  staged?: GitFile[];
  unstaged?: GitFile[];
  untracked?: GitFile[];
  files: GitFile[];
  log: GitLogEntry[];
  ahead?: number;
  behind?: number;
  hasRemote?: boolean;
  upstream?: string;
  error?: string;
}

export interface SecretsProviderStatus {
  provider: string;
  definedSecretGroups: string[];
  containerIdentities: Record<string, boolean>;
  warnings: string[];
}

export interface SecretEntry {
  name: string;
  source: string;
}

export interface SecretScopeStatus {
  scopeType: "shared" | "group" | "container";
  scopeId: string;
  entries: SecretEntry[];
  canRotate: boolean;
  warnings: string[];
}

export interface FilePreview {
  path: string;
  content: string;
}

export interface AppSnapshot {
  configPath: string;
  configExists: boolean;
  siteDirExists: boolean;
  siteNixPath: string;
  siteNixExists: boolean;
  siteNixContent: string;
  preservedConfigKeys: string[];
  warnings: string[];
  config: ProxnixConfig;
  containers: ContainerSummary[];
  definedSecretGroups: string[];
  attachedSecretGroups: string[];
  sidebarMetadata: Record<string, SidebarMetadata>;
}

export type ProxnixManagerRPC = {
  bun: RPCSchema<{
    requests: {
      loadSnapshot: {
        params:
          | {
              force?: boolean;
            }
          | undefined;
        response: AppSnapshot;
      };
      saveConfig: {
        params: {
          config: ProxnixConfig;
        };
        response: AppSnapshot;
      };
      runOnboarding: {
        params: {
          config: ProxnixConfig;
        };
        response: OnboardingResult;
      };
      createSiteNix: {
        params: void;
        response: AppSnapshot;
      };
      chooseSiteDirectory: {
        params:
          | {
              startingFolder?: string;
            }
          | undefined;
        response: string | null;
      };
      openPath: {
        params: {
          path: string;
        };
        response: boolean;
      };
      saveSidebarMetadata: {
        params: {
          vmid: string;
          metadata: SidebarMetadata;
        };
        response: AppSnapshot;
      };
      loadSecretsProviderStatus: {
        params:
          | {
              force?: boolean;
            }
          | undefined;
        response: SecretsProviderStatus;
      };
      loadSecretScopeStatus: {
        params: {
          scopeType: "shared" | "group" | "container";
          scopeId?: string;
          force?: boolean;
        };
        response: SecretScopeStatus;
      };
      setSecret: {
        params: {
          scopeType: "shared" | "group" | "container";
          scopeId?: string;
          name: string;
          value: string;
        };
        response: CommandResult;
      };
      removeSecret: {
        params: {
          scopeType: "shared" | "group" | "container";
          scopeId?: string;
          name: string;
        };
        response: CommandResult;
      };
      rotateSecretScope: {
        params: {
          scopeType: "shared" | "group" | "container";
          scopeId?: string;
        };
        response: CommandResult;
      };
      initContainerIdentity: {
        params: {
          vmid: string;
        };
        response: CommandResult;
      };
      createContainerBundle: {
        params: {
          vmid: string;
        };
        response: AppSnapshot;
      };
      deleteContainerBundle: {
        params: {
          vmid: string;
        };
        response: AppSnapshot;
      };
      createSecretGroup: {
        params: {
          group: string;
        };
        response: AppSnapshot;
      };
      deleteSecretGroup: {
        params: {
          group: string;
        };
        response: AppSnapshot;
      };
      attachSecretGroup: {
        params: {
          vmid: string;
          group: string;
        };
        response: AppSnapshot;
      };
      detachSecretGroup: {
        params: {
          vmid: string;
          group: string;
        };
        response: AppSnapshot;
      };
      runDoctor: {
        params: { configOnly?: boolean; vmid?: string };
        response: DoctorResult;
      };
      runPublish: {
        params: { dryRun?: boolean; configOnly?: boolean; vmid?: string; hosts?: string[] };
        response: CommandResult;
      };
      gitStatus: {
        params: void;
        response: GitStatusResult;
      };
      gitAdd: {
        params: { all?: boolean; file?: string };
        response: CommandResult;
      };
      gitCommit: {
        params: { message: string };
        response: CommandResult;
      };
      gitPush: {
        params: void;
        response: CommandResult;
      };
      openInEditor: {
        params: { path: string };
        response: { opened: boolean; editor?: string; error?: string };
      };
      readTextFile: {
        params: { path: string };
        response: FilePreview;
      };
      loadProxmoxNodes: {
        params: void;
        response: ProxmoxNodesResult;
      };
      restartProxmoxContainer: {
        params: { vmid: string };
        response: CommandResult;
      };
    };
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {};
  }>;
};
