import type { ProxnixManagerRPC } from "../types/types";

type RequestMap = ProxnixManagerRPC["bun"] extends { requests: infer Requests } ? Requests : never;

export interface ProxnixRpcClient {
  request: {
    [K in keyof RequestMap]: RequestMap[K] extends { params: infer Params; response: infer Response }
      ? (params: Params) => Promise<Response>
      : never;
  };
}
