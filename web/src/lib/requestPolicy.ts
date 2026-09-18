interface BrowserBoundary {
  mode?: string;
  host: string;
  origin: string | null;
  fetchSite: string | null;
  allowedHosts?: string;
  allowedOrigins?: string;
}

/** Browser-facing boundary, before Next rewrites the Host to the API address.
 * This is not authentication: cloud API requests must also carry a bearer token
 * supplied by a trusted gateway, never injected by this proxy.
 */
export function trustedBrowserRequest(request: BrowserBoundary): boolean {
  const mode = request.mode ?? "local";
  if (mode !== "local" && mode !== "cloud") return false;
  const local = mode === "local";
  const host = request.host.toLowerCase();
  const hosts = (request.allowedHosts ?? "").split(",").map((h) => h.trim().toLowerCase());
  const localHost = /^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/;
  const defaultOrigins = ["localhost", "127.0.0.1", "[::1]"].map((h) => `http://${h}:3000`);
  const origins = request.allowedOrigins?.split(",").map((o) => o.trim())
    ?? (local ? defaultOrigins : []);
  return (local ? localHost.test(host) : !!host && hosts.includes(host))
    && (request.origin === null || origins.includes(request.origin))
    && request.fetchSite !== "cross-site";
}