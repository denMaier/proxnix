export interface ProxmoxNodeSummary {
  node: string;
  status: string;
  type: string;
  cpu: number | null;
  maxcpu: number | null;
  mem: number | null;
  maxmem: number | null;
  disk: number | null;
  maxdisk: number | null;
  uptime: number | null;
}

export interface ProxmoxContainerStatus {
  vmid: string;
  node: string;
  name: string;
  status: string;
  uptime: number | null;
}

export interface ProxmoxNodesResult {
  configured: boolean;
  apiUrl: string;
  nodes: ProxmoxNodeSummary[];
  containers: ProxmoxContainerStatus[];
  warnings: string[];
}
