import type { ProxnixRpcClient } from "./rpcTypes";

interface WebRpcEnvelope {
  ok: boolean;
  result?: unknown;
  error?: string;
}

export function createWebRpcClient(): ProxnixRpcClient {
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
