import { Electroview } from "electrobun/view";
import type { ProxnixManagerRPC } from "../types/types";
import type { ProxnixRpcClient } from "./rpcTypes";

export function createDesktopRpcClient(maxRequestTime: number): ProxnixRpcClient {
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
