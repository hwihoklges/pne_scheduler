import type { NextConfig } from "next";

// The API runs as a separate process on the same machine (see WEB_PORT_PLAN §3).
// Proxying keeps the browser on one origin, so there is no CORS surface and no
// reason for the API to accept requests from anywhere else.
const API = process.env.PNE_API ?? "http://127.0.0.1:8000";
const mode = process.env.PNE_SERVER_MODE ?? "local";
if (!["local", "cloud"].includes(mode)) throw new Error("Invalid PNE_SERVER_MODE");
const target = new URL(API);
if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) {
  throw new Error("PNE_API must be an HTTP(S) URL without credentials");
}
if (mode === "local" && !["localhost", "127.0.0.1", "[::1]"].includes(target.hostname)) {
  throw new Error("Local mode requires a loopback PNE_API");
}
// Never inject PNE_API_TOKEN here: a public proxy would become an authenticated
// filesystem/API relay. Cloud authentication belongs to a trusted TLS gateway.

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default config;
