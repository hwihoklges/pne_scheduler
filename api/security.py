"""Single-user local boundary, or authenticated stateless cloud-safe mode.

PNE_SERVER_MODE=local (default) requires loopback peers and Host, and permits
local-resource routes unless PNE_LOCAL_RESOURCES=0. Cloud mode always disables
those routes, requires PNE_API_TOKEN (at least 32 characters), and exact
PNE_ALLOWED_HOSTS authorities. PNE_ALLOWED_ORIGINS is a comma-separated list of
browser origins (local defaults: loopback ports 3000/8000). Forwarded headers are
never trusted. Cloud deployments need a TLS/auth gateway; this is not tenant
isolation, and tokens must never be exposed by a public Next proxy or browser
storage. Configuration is read only at application creation.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import ApiError


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _authority_host(authority: str) -> str:
    try:
        parsed = urlsplit("http://" + authority)
        _ = parsed.port  # validate malformed ports as well as the hostname
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return ""
        return parsed.hostname or ""
    except ValueError:
        return ""


@dataclass(frozen=True)
class AccessPolicy:
    mode: str
    local_resources: bool
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    token: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, *, mode: str | None = None,
                         local_resources: bool | None = None) -> AccessPolicy:
        mode = mode if mode is not None else os.getenv("PNE_SERVER_MODE", "local")
        if mode not in {"local", "cloud"}:
            raise ValueError("PNE_SERVER_MODE must be local or cloud")
        flag = os.getenv("PNE_LOCAL_RESOURCES", "1" if mode == "local" else "0")
        if flag not in {"0", "1"}:
            raise ValueError("PNE_LOCAL_RESOURCES must be 0 or 1")
        enabled = flag == "1" if local_resources is None else local_resources
        if mode == "cloud" and enabled:
            raise ValueError("Local resources cannot be enabled in cloud mode")
        token = os.getenv("PNE_API_TOKEN", "") if mode == "cloud" else ""
        if mode == "cloud" and len(token) < 32:
            raise ValueError("Cloud mode requires PNE_API_TOKEN (at least 32 characters)")
        hosts = frozenset(h.strip().lower() for h in os.getenv("PNE_ALLOWED_HOSTS", "").split(",") if h.strip())
        if any(not _authority_host(h) for h in hosts) or (mode == "cloud" and not hosts):
            raise ValueError("Cloud mode requires exact valid PNE_ALLOWED_HOSTS authorities")
        defaults = ",".join(
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1", "[::1]") for port in (3000, 8000)
        ) if mode == "local" else ""
        origins = frozenset(o.strip() for o in os.getenv("PNE_ALLOWED_ORIGINS", defaults).split(",") if o.strip())
        for origin in origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not _authority_host(parsed.netloc)
                    or origin != f"{parsed.scheme}://{parsed.netloc}"
                    or (mode == "local" and not is_loopback_host(parsed.hostname or ""))):
                raise ValueError("PNE_ALLOWED_ORIGINS must contain exact trusted origins")
        return cls(mode, bool(enabled), hosts, origins, token)

    def check_request(self, *, host: str, remote_addr: str | None,
                      origin: str | None, fetch_site: str | None,
                      authorization: str) -> None:
        hostname = _authority_host(host)
        if self.mode == "local":
            if not is_loopback_host(hostname) or not is_loopback_host(remote_addr or ""):
                raise ApiError("Local API requires a loopback host and peer", status=403)
        elif host.lower() not in self.allowed_hosts:
            raise ApiError("Untrusted Host", status=403)
        if origin is not None and origin not in self.allowed_origins:
            raise ApiError("Untrusted Origin", status=403)
        if fetch_site == "cross-site":
            raise ApiError("Cross-site requests are not permitted", status=403)
        if self.mode == "cloud":
            expected = ("Bearer " + self.token).encode("utf-8")
            if not hmac.compare_digest(authorization.encode("utf-8"), expected):
                raise ApiError("Authentication required", status=401)