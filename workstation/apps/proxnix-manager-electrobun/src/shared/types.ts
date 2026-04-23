import type { RPCSchema } from "electrobun/bun";

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
}

export interface ContainerSummary {
  vmid: string;
  containerPath: string;
  privateContainerPath: string;
  dropins: string[];
  hasConfig: boolean;
  hasSecretStore: boolean;
  hasIdentity: boolean;
  secretGroups: string[];
}

export interface AppSnapshot {
  configPath: string;
  configExists: boolean;
  siteDirExists: boolean;
  preservedConfigKeys: string[];
  warnings: string[];
  config: ProxnixConfig;
  containers: ContainerSummary[];
  definedSecretGroups: string[];
  attachedSecretGroups: string[];
}

export type ProxnixManagerRPC = {
  bun: RPCSchema<{
    requests: {
      loadSnapshot: {
        params: void;
        response: AppSnapshot;
      };
      saveConfig: {
        params: {
          config: ProxnixConfig;
        };
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
    };
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {};
  }>;
};
