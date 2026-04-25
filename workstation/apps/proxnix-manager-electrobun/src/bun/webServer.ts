import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createManagerRequestHandlers } from "./managerHandlers";

type RpcHandler = (params: unknown) => unknown | Promise<unknown>;

interface WebOptions {
  host: string;
  port: number;
}

const SRC_ROOT = new URL("../", import.meta.url);
const MAINVIEW_ROOT = new URL("./mainview/", SRC_ROOT);

const MIME_TYPES: Record<string, string> = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

const handlers = createManagerRequestHandlers({
  chooseSiteDirectory: async () => null,
  openPath: () => false,
  openInEditor: async (params) => ({
    opened: false,
    error: `Editor launching is only available in the desktop app: ${params.path}`,
  }),
}) as Record<string, RpcHandler>;

const options = parseArgs(process.argv.slice(2));
const publicDir = await buildMainviewBundle();

if (!isLoopbackHost(options.host) && !process.env.PROXNIX_MANAGER_TRUSTED_AUTH_HEADER) {
  console.warn(
    "warning: web mode is listening on a non-loopback address without PROXNIX_MANAGER_TRUSTED_AUTH_HEADER set; put an auth proxy in front of this service.",
  );
}

const server = Bun.serve({
  hostname: options.host,
  port: options.port,
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/api/rpc") {
      return handleRpc(request);
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return textResponse("Method not allowed", 405);
    }

    if (url.pathname === "/api/session") {
      return jsonResponse({
        mode: "web",
        user: trustedProxyUser(request),
        authHeader: process.env.PROXNIX_MANAGER_TRUSTED_AUTH_HEADER || "",
      });
    }

    return serveStatic(url.pathname);
  },
});

console.log(`Proxnix Manager web mode listening on http://${server.hostname}:${server.port}`);
console.log("Use a reverse auth proxy for non-local access.");

function parseArgs(args: string[]): WebOptions {
  let host = process.env.PROXNIX_MANAGER_WEB_HOST || "127.0.0.1";
  let port = Number(process.env.PROXNIX_MANAGER_WEB_PORT || "4173");

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--host") {
      host = args[++index] || host;
    } else if (arg === "--port") {
      port = Number(args[++index] || port);
    } else if (arg === "--listen") {
      const value = args[++index] || "";
      const [hostPart, portPart] = value.split(":");
      host = hostPart || host;
      port = Number(portPart || port);
    } else if (arg === "--help" || arg === "-h") {
      printUsage();
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }

  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`invalid port: ${port}`);
  }

  return { host, port };
}

function printUsage(): void {
  console.log(`Usage:
  bun run src/bun/webServer.ts [--host <host>] [--port <port>]
  bun run web -- --host 127.0.0.1 --port 4173

Environment:
  PROXNIX_MANAGER_WEB_HOST              Default host, 127.0.0.1
  PROXNIX_MANAGER_WEB_PORT              Default port, 4173
  PROXNIX_MANAGER_TRUSTED_AUTH_HEADER   Optional identity header set by a trusted auth proxy
`);
}

async function buildMainviewBundle(): Promise<string> {
  const outdir = await mkdtemp(join(tmpdir(), "proxnix-manager-web."));
  const result = await Bun.build({
    entrypoints: [new URL("./mainview/index.ts", SRC_ROOT).pathname],
    outdir,
    target: "browser",
    format: "esm",
    sourcemap: "linked",
    minify: false,
  });

  if (!result.success) {
    const messages = result.logs.map((log) => log.message).join("\n");
    throw new Error(`failed to build web bundle\n${messages}`);
  }

  await Bun.write(
    join(outdir, "index.html"),
    `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Proxnix Manager</title>
    <link rel="stylesheet" href="/index.css" />
    <script>window.__PROXNIX_WEB__ = true;</script>
    <script type="module" src="/index.js"></script>
  </head>
  <body>
    <div id="app"></div>
  </body>
</html>
`,
  );

  return outdir;
}

async function handleRpc(request: Request): Promise<Response> {
  if (!isSameOrigin(request)) {
    return textResponse("Bad origin", 403);
  }

  let payload: { method?: unknown; params?: unknown };
  try {
    payload = (await request.json()) as { method?: unknown; params?: unknown };
  } catch {
    return jsonResponse({ ok: false, error: "invalid JSON request" }, 400);
  }

  if (typeof payload.method !== "string") {
    return jsonResponse({ ok: false, error: "missing RPC method" }, 400);
  }

  const handler = handlers[payload.method];
  if (!handler) {
    return jsonResponse({ ok: false, error: `unknown RPC method: ${payload.method}` }, 404);
  }

  try {
    const result = await handler(payload.params);
    return jsonResponse({ ok: true, result });
  } catch (error) {
    return jsonResponse({ ok: false, error: errorMessage(error) }, 500);
  }
}

function serveStatic(pathname: string): Response {
  const path = pathname === "/" ? "/index.html" : pathname;

  if (path.includes("..")) {
    return textResponse("Not found", 404);
  }

  if (path === "/index.css") {
    return fileResponse(new URL("./index.css", MAINVIEW_ROOT).pathname);
  }

  if (path.startsWith("/assets/")) {
    return fileResponse(new URL(`.${path}`, MAINVIEW_ROOT).pathname);
  }

  return fileResponse(join(publicDir, path.replace(/^\/+/, "")));
}

function fileResponse(path: string): Response {
  const file = Bun.file(path);
  return new Response(file, {
    headers: securityHeaders({ "Content-Type": contentType(path) }),
  });
}

function textResponse(text: string, status: number): Response {
  return new Response(text, {
    status,
    headers: securityHeaders({ "Content-Type": "text/plain; charset=utf-8" }),
  });
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: securityHeaders({ "Content-Type": "application/json; charset=utf-8" }),
  });
}

function securityHeaders(headers: Record<string, string>): Headers {
  return new Headers({
    ...headers,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
  });
}

function contentType(path: string): string {
  const match = path.match(/\.[^.]+$/);
  return (match && MIME_TYPES[match[0]]) || "application/octet-stream";
}

function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return true;
  }
  return origin === new URL(request.url).origin;
}

function trustedProxyUser(request: Request): string {
  const header = process.env.PROXNIX_MANAGER_TRUSTED_AUTH_HEADER;
  if (!header) {
    return "";
  }
  return request.headers.get(header) || "";
}

function isLoopbackHost(host: string): boolean {
  return host === "127.0.0.1" || host === "localhost" || host === "::1";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
