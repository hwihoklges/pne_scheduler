import { NextRequest, NextResponse } from "next/server";
import { trustedBrowserRequest } from "./lib/requestPolicy";

// Check the browser-facing Host *before* rewrites replace it with the API host.
// Never trust X-Forwarded-Host, and never add a server bearer token to requests.
export function middleware(request: NextRequest) {
  if (!trustedBrowserRequest({
    mode: process.env.PNE_SERVER_MODE,
    host: request.headers.get("host") ?? "",
    origin: request.headers.get("origin"),
    fetchSite: request.headers.get("sec-fetch-site"),
    allowedHosts: process.env.PNE_ALLOWED_HOSTS,
    allowedOrigins: process.env.PNE_ALLOWED_ORIGINS,
  })) {
    return NextResponse.json({ ok: false, error: "Untrusted browser request" }, { status: 403 });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };