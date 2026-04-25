import { request } from "node:https";
import type {
  ProxmoxContainerStatus,
  ProxmoxNodeSummary,
} from "../shared/proxmoxTypes";

export type ProxmoxClientConfig = {
  enabled: boolean;
  apiUrl: string;
  tokenId: string;
  tokenSecret: string;
  verifyTls: boolean;
};

type ProxmoxEnvelope = {
  data?: unknown;
};

function asNullableNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function apiPath(baseUrl: string, path: string): URL {
  return new URL(`api2/json/${path.replace(/^\/+/u, "")}`, `${baseUrl.replace(/\/+$/u, "")}/`);
}

export function missingProxmoxConfig(config: ProxmoxClientConfig): string[] {
  const missing: string[] = [];
  if (!config.apiUrl.trim()) missing.push("Proxmox API URL");
  if (!config.tokenId.trim()) missing.push("API token ID");
  if (!config.tokenSecret.trim()) missing.push("API token secret");
  return missing;
}

export async function proxmoxRequest(
  config: ProxmoxClientConfig,
  path: string,
  options: { method?: "GET" | "POST" } = {},
): Promise<unknown> {
  const method = options.method ?? "GET";
  const url = apiPath(config.apiUrl, path);

  return await new Promise((resolve, reject) => {
    const req = request(
      url,
      {
        method,
        rejectUnauthorized: config.verifyTls,
        timeout: 30_000,
        headers: {
          Accept: "application/json",
          Authorization: `PVEAPIToken=${config.tokenId}=${config.tokenSecret}`,
        },
      },
      (res) => {
        const chunks: string[] = [];
        res.on("data", (chunk: Buffer | string) => {
          chunks.push(typeof chunk === "string" ? chunk : chunk.toString("utf8"));
        });
        res.on("end", () => {
          const body = chunks.join("").trim();
          if ((res.statusCode ?? 500) >= 400) {
            reject(new Error(`Proxmox API returned HTTP ${res.statusCode}: ${body || res.statusMessage || ""}`));
            return;
          }

          let envelope: ProxmoxEnvelope;
          try {
            envelope = JSON.parse(body) as ProxmoxEnvelope;
          } catch (error) {
            reject(new Error(`Proxmox API returned invalid JSON: ${String(error)}`));
            return;
          }

          resolve(envelope.data);
        });
      },
    );

    req.on("timeout", () => {
      req.destroy(new Error("Proxmox API request timed out"));
    });
    req.on("error", reject);
    req.end();
  });
}

export function normalizeNode(raw: unknown): ProxmoxNodeSummary | null {
  const record = asRecord(raw);
  if (!record) return null;
  const node = String(record.node ?? "").trim();
  if (!node) return null;
  return {
    node,
    status: String(record.status ?? "").trim() || "unknown",
    type: String(record.type ?? "").trim() || "node",
    cpu: asNullableNumber(record.cpu),
    maxcpu: asNullableNumber(record.maxcpu),
    mem: asNullableNumber(record.mem),
    maxmem: asNullableNumber(record.maxmem),
    disk: asNullableNumber(record.disk),
    maxdisk: asNullableNumber(record.maxdisk),
    uptime: asNullableNumber(record.uptime),
  };
}

export function normalizeContainer(raw: unknown, node: string): ProxmoxContainerStatus | null {
  const record = asRecord(raw);
  if (!record) return null;
  const vmid = String(record.vmid ?? "").trim();
  if (!vmid) return null;
  return {
    vmid,
    node,
    name: String(record.name ?? "").trim(),
    status: String(record.status ?? "").trim() || "unknown",
    uptime: asNullableNumber(record.uptime),
  };
}
