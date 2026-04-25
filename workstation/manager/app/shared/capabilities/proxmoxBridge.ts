import type { CommandResult } from "../types/types";
import type { ProxmoxContainerStatus, ProxmoxNodeSummary, ProxmoxNodesResult } from "../types/proxmoxTypes";
import {
  missingProxmoxConfig,
  normalizeContainer,
  normalizeNode,
  proxmoxRequest,
  type ProxmoxClientConfig,
} from "./proxmoxClient";
import { loadSnapshot } from "./workstationBridge";

function enabled(value: string): boolean {
  return ["1", "true", "yes", "on"].includes(value.trim().toLocaleLowerCase());
}

function clientConfig(): ProxmoxClientConfig {
  const config = loadSnapshot().config;
  return {
    enabled: enabled(config.proxmoxApiEnabled),
    apiUrl: config.proxmoxApiUrl.trim(),
    tokenId: config.proxmoxApiTokenId.trim(),
    tokenSecret: config.proxmoxApiTokenSecret.trim(),
    verifyTls: !["0", "false", "no", "off"].includes(config.proxmoxVerifyTls.trim().toLocaleLowerCase()),
  };
}

function disabledResult(config: ProxmoxClientConfig, warnings: string[]): ProxmoxNodesResult {
  return {
    configured: false,
    apiUrl: config.apiUrl,
    nodes: [],
    containers: [],
    warnings,
  };
}

async function loadContainersForNode(
  config: ProxmoxClientConfig,
  node: string,
  warnings: string[],
): Promise<ProxmoxContainerStatus[]> {
  try {
    const raw = await proxmoxRequest(config, `nodes/${encodeURIComponent(node)}/lxc`);
    if (!Array.isArray(raw)) {
      warnings.push(`Proxmox API returned non-list LXC payload for ${node}.`);
      return [];
    }
    return raw
      .map((entry) => normalizeContainer(entry, node))
      .filter((entry): entry is ProxmoxContainerStatus => entry !== null);
  } catch (error) {
    warnings.push(`Could not load LXC status for ${node}: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}

export async function loadProxmoxNodes(): Promise<ProxmoxNodesResult> {
  const config = clientConfig();
  if (!config.enabled) {
    return disabledResult(config, ["Enable Proxmox API integration in Settings."]);
  }

  const missing = missingProxmoxConfig(config);
  if (missing.length > 0) {
    return disabledResult(config, [`Complete Proxmox API settings: ${missing.join(", ")}.`]);
  }

  const rawNodes = await proxmoxRequest(config, "nodes");
  if (!Array.isArray(rawNodes)) {
    throw new Error("Proxmox API returned a non-list nodes payload.");
  }

  const warnings: string[] = [];
  const nodes = rawNodes
    .map((entry) => normalizeNode(entry))
    .filter((entry): entry is ProxmoxNodeSummary => entry !== null)
    .sort((left, right) => left.node.localeCompare(right.node, undefined, { numeric: true }));
  const containerGroups = await Promise.all(
    nodes.map((node) => loadContainersForNode(config, node.node, warnings)),
  );

  return {
    configured: true,
    apiUrl: config.apiUrl,
    nodes,
    containers: containerGroups
      .flat()
      .sort((left, right) =>
        left.node.localeCompare(right.node, undefined, { numeric: true }) ||
        left.vmid.localeCompare(right.vmid, undefined, { numeric: true }),
      ),
    warnings,
  };
}

export async function restartProxmoxContainer(vmid: string): Promise<CommandResult> {
  const cleanVmid = vmid.trim();
  if (!/^\d+$/u.test(cleanVmid)) {
    return { output: "", exitCode: 1, error: "Container VMID is required." };
  }

  const inventory = await loadProxmoxNodes();
  if (!inventory.configured) {
    return { output: "", exitCode: 1, error: inventory.warnings[0] ?? "Proxmox API is not configured." };
  }

  const container = inventory.containers.find((entry) => entry.vmid === cleanVmid);
  if (!container) {
    return { output: "", exitCode: 1, error: `Container ${cleanVmid} is not deployed in Proxmox.` };
  }

  const config = clientConfig();
  try {
    await proxmoxRequest(
      config,
      `nodes/${encodeURIComponent(container.node)}/lxc/${encodeURIComponent(cleanVmid)}/status/reboot`,
      { method: "POST" },
    );
  } catch (error) {
    return { output: "", exitCode: 1, error: error instanceof Error ? error.message : String(error) };
  }

  return {
    output: `Restart requested for container ${cleanVmid} on ${container.node}.`,
    exitCode: 0,
    error: "",
  };
}
