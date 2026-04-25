import { loadProxmoxNodes, restartProxmoxContainer } from "./proxmoxBridge";
import type { ProxnixConfig, SidebarMetadata } from "../types/types";
import {
  attachSecretGroup,
  createContainerBundle,
  createSecretGroup,
  createSiteNix,
  deleteContainerBundle,
  deleteSecretGroup,
  detachSecretGroup,
  gitAdd,
  gitCommit,
  gitPush,
  gitStatus,
  initContainerIdentity,
  loadSecretScopeStatus,
  loadSecretsProviderStatus,
  loadSnapshot,
  readTextFile,
  removeSecret,
  rotateSecretScope,
  runDoctor,
  runOnboarding,
  runPublish,
  saveConfig,
  saveSidebarMetadata,
  setSecret,
} from "./workstationBridge";

export const INTERACTIVE_BACKEND_REQUEST_TIMEOUT_MS = 60 * 60 * 1000;

export type ManagerRequestHandlers = ReturnType<typeof createManagerRequestHandlers>;

export interface ManagerPlatformHandlers {
  chooseSiteDirectory(params: { startingFolder?: string } | undefined): Promise<string | null>;
  openPath(params: { path: string }): Promise<boolean> | boolean;
  openInEditor(params: { path: string }): Promise<{ opened: boolean; editor?: string; error?: string }>;
}

export function createManagerRequestHandlers(platform: ManagerPlatformHandlers) {
  return {
    loadSnapshot: (params: { force?: boolean } | undefined) => loadSnapshot(params),
    saveConfig: (params: { config: ProxnixConfig }) => saveConfig(params.config),
    runOnboarding: (params: { config: ProxnixConfig }) => runOnboarding(params.config),
    createSiteNix: (_params: void) => createSiteNix(),
    chooseSiteDirectory: (params: { startingFolder?: string } | undefined) => platform.chooseSiteDirectory(params),
    openPath: (params: { path: string }) => platform.openPath(params),
    saveSidebarMetadata: (params: { vmid: string; metadata: SidebarMetadata }) =>
      saveSidebarMetadata(params.vmid, params.metadata),
    loadSecretsProviderStatus: (params: { force?: boolean } | undefined) => loadSecretsProviderStatus(params),
    loadSecretScopeStatus: (params: Parameters<typeof loadSecretScopeStatus>[0]) => loadSecretScopeStatus(params),
    setSecret: (params: Parameters<typeof setSecret>[0]) => setSecret(params),
    removeSecret: (params: Parameters<typeof removeSecret>[0]) => removeSecret(params),
    rotateSecretScope: (params: Parameters<typeof rotateSecretScope>[0]) => rotateSecretScope(params),
    initContainerIdentity: (params: { vmid: string }) => initContainerIdentity(params.vmid),
    createContainerBundle: (params: { vmid: string }) => createContainerBundle(params.vmid),
    deleteContainerBundle: (params: { vmid: string }) => deleteContainerBundle(params.vmid),
    createSecretGroup: (params: { group: string }) => createSecretGroup(params.group),
    deleteSecretGroup: (params: { group: string }) => deleteSecretGroup(params.group),
    attachSecretGroup: (params: { vmid: string; group: string }) => attachSecretGroup(params.vmid, params.group),
    detachSecretGroup: (params: { vmid: string; group: string }) => detachSecretGroup(params.vmid, params.group),
    runDoctor: (params: Parameters<typeof runDoctor>[0]) => runDoctor(params),
    runPublish: (params: Parameters<typeof runPublish>[0]) => runPublish(params),
    gitStatus: (_params: void) => gitStatus(),
    gitAdd: (params: Parameters<typeof gitAdd>[0]) => gitAdd(params),
    gitCommit: (params: { message: string }) => gitCommit(params.message),
    gitPush: (_params: void) => gitPush(),
    readTextFile: (params: { path: string }) => readTextFile(params.path),
    loadProxmoxNodes: (_params: void) => loadProxmoxNodes(),
    restartProxmoxContainer: (params: { vmid: string }) => restartProxmoxContainer(params.vmid),
    openInEditor: (params: { path: string }) => platform.openInEditor(params),
  };
}
