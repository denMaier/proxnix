import type { ProxnixManagerRPC } from "../shared/types";

type RequestMap = ProxnixManagerRPC["bun"] extends { requests: infer Requests } ? Requests : never;

export interface ProxnixRpcClient {
  request: {
    [K in keyof RequestMap]: RequestMap[K] extends { params: infer Params; response: infer Response }
      ? (params: Params) => Promise<Response>
      : never;
  };
}

interface WebRpcEnvelope {
  ok: boolean;
  result?: unknown;
  error?: string;
}

declare global {
  interface Window {
    __PROXNIX_WEB__?: boolean;
  }
}

export async function createProxnixRpcClient(maxRequestTime: number): Promise<ProxnixRpcClient> {
  if (window.__PROXNIX_WEB__) {
    return createWebRpcClient();
  }

  const loadElectrobunView = new Function("specifier", "return import(specifier)") as (
    specifier: string,
  ) => Promise<typeof import("electrobun/view")>;
  const { Electroview } = await loadElectrobunView("electrobun/view");
  const proxnixRpc = Electroview.defineRPC<ProxnixManagerRPC>({
    maxRequestTime,
    handlers: {
      requests: {},
      messages: {},
    },
  });
  new Electroview({ rpc: proxnixRpc });
  return proxnixRpc as ProxnixRpcClient;
}

function createWebRpcClient(): ProxnixRpcClient {
  return {
    request: new Proxy(
      {},
      {
        get(_target, prop) {
          if (typeof prop !== "string") {
            return undefined;
          }
          return async (params: unknown) => {
            const response = await fetch("/api/rpc", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({ method: prop, params }),
            });

            let envelope: WebRpcEnvelope;
            try {
              envelope = (await response.json()) as WebRpcEnvelope;
            } catch {
              throw new Error(`RPC ${prop} failed with HTTP ${response.status}`);
            }

            if (!response.ok || !envelope.ok) {
              throw new Error(envelope.error || `RPC ${prop} failed with HTTP ${response.status}`);
            }

            return envelope.result;
          };
        },
      },
    ) as ProxnixRpcClient["request"],
  };
}
