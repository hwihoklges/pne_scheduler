"""Flask application wiring the route groups to HTTP.

Flask rather than a heavier framework because the payloads are plain JSON that
`ui/workspace_model.py` already validates, and because the whole surface is
around a dozen routes. It is an optional `[web]` extra: nothing in the package
imports it unless the server is actually started.

Import sessions are the one piece of server state. They hold the source bytes and
digest of a real `.sch`, which is exactly what must not be re-read casually, so
they live in a bounded in-process registry and expire.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from ..import_session import ImportSession
from ..library import MethodLibrary
from . import routes
from .errors import ApiError
from .security import AccessPolicy, is_loopback_host

# An import session is a held-open file, not a login. It expires so a forgotten
# browser tab cannot pin a lab file's bytes in memory for a day.
SESSION_TTL_SECONDS = 3600
SESSION_LIMIT = 16


class SessionRegistry:
    """In-process import sessions, bounded and expiring."""

    def __init__(self, ttl: float = SESSION_TTL_SECONDS, limit: int = SESSION_LIMIT):
        self.ttl = ttl
        self.limit = limit
        self._items: dict[str, tuple[float, ImportSession]] = {}

    def _sweep(self) -> None:
        now = time.monotonic()
        for key in [k for k, (t, _) in self._items.items() if now - t > self.ttl]:
            del self._items[key]
        while len(self._items) > self.limit:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            del self._items[oldest]

    def add(self, session: ImportSession) -> str:
        self._sweep()
        key = uuid.uuid4().hex[:12]
        self._items[key] = (time.monotonic(), session)
        return key

    def get(self, key: str) -> ImportSession:
        self._sweep()
        found = self._items.get(key)
        if found is None:
            raise ApiError("import 세션이 만료되었거나 없습니다. 다시 여십시오.", status=404)
        return found[1]


def create_app(library_root: Path | None = None, *, mode: str | None = None,
               local_resources: bool | None = None) -> Any:
    """Create a backwards-compatible local app; see security.AccessPolicy for env.

    Cloud mode authenticates all routes and never constructs a local library or
    exposes filesystem/import sessions. No bearer token is returned to clients.
    """
    from flask import Flask, jsonify, request
    from werkzeug.exceptions import HTTPException

    app = Flask(__name__)
    policy = AccessPolicy.from_environment(mode=mode, local_resources=local_resources)
    app.config["PNE_SERVER_MODE"] = policy.mode
    store = MethodLibrary(library_root) if policy.local_resources else None
    sessions = SessionRegistry()

    @app.before_request
    def _guard():
        policy.check_request(
            host=request.host, remote_addr=request.remote_addr,
            origin=request.headers.get("Origin"),
            fetch_site=request.headers.get("Sec-Fetch-Site"),
            authorization=request.headers.get("Authorization", ""),
        )
        if routes.is_local_resource_path(request.path) and not policy.local_resources:
            raise ApiError("Local-resource routes are disabled", status=403)

    @app.errorhandler(ApiError)
    def _handle(error: ApiError):
        return jsonify(error.as_dict()), error.status

    @app.errorhandler(Exception)
    def _unexpected(error: Exception):
        # A bug must still reach the client as JSON: an HTML traceback page in a
        # fetch() response surfaces as an unreadable parse error instead.
        if isinstance(error, HTTPException):
            return jsonify({"ok": False, "error": error.name}), error.code
        app.logger.exception("unhandled API error")
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    def body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError("JSON 본문이 필요합니다.")
        return data

    # ---- derive ----------------------------------------------------------

    @app.post("/api/views")
    def _views():
        return jsonify(routes.views(body()))

    @app.post("/api/steps")
    def _steps():
        return jsonify(routes.steps(body()))

    # ---- transform -------------------------------------------------------

    @app.post("/api/edit/<action>")
    def _edit(action: str):
        return jsonify(routes.transform(action, body()))

    # ---- plan ------------------------------------------------------------

    @app.post("/api/plan/<action>")
    def _plan(action: str):
        return jsonify(routes.plan(action, body()))

    # ---- local resources -------------------------------------------------

    @app.get("/api/library")
    def _library():
        return jsonify(routes.library_list(store))

    @app.get("/api/library/<method_id>")
    def _library_versions(method_id: str):
        return jsonify(routes.library_versions(store, method_id))

    @app.post("/api/library")
    def _library_save():
        return jsonify(routes.library_save(store, body()))

    @app.post("/api/library/load")
    def _library_load():
        return jsonify(routes.library_load(store, body()))

    @app.post("/api/import/open")
    def _import_open():
        payload = body()
        session, info = routes.import_open(str(payload.get("path", "")))
        info["sessionId"] = sessions.add(session)
        return jsonify(info)

    @app.post("/api/import/<session_id>/stage")
    def _import_stage(session_id: str):
        payload = body()
        session = sessions.get(session_id)
        try:
            session.stage(
                int(payload["stepNo"]), str(payload["field"]), float(payload["value"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError(f"편집 값이 잘못되었습니다: {exc}") from exc
        return jsonify(routes.import_proposal(session))

    @app.post("/api/import/<session_id>/clear")
    def _import_clear(session_id: str):
        session = sessions.get(session_id)
        session.clear()
        return jsonify(routes.import_proposal(session))

    @app.post("/api/import/<session_id>/plan")
    def _import_plan(session_id: str):
        return jsonify(routes.import_proposal(sessions.get(session_id)))

    @app.post("/api/export")
    def _export():
        return jsonify(routes.export(body()))

    @app.get("/api/health")
    def _health():
        return jsonify({"ok": True, "mode": policy.mode,
                        "localResources": policy.local_resources})

    return app


def main(host: str = "127.0.0.1", port: int = 8000) -> int:
    """Serve on loopback by default; local mode forbids a routable bind.

    PNE_SERVER_MODE=cloud can be hosted separately behind a TLS/auth gateway,
    with filesystem routes disabled and PNE_API_TOKEN/PNE_ALLOWED_HOSTS set.
    This development server is not a production multi-user deployment stack.
    """
    app = create_app()
    if app.config["PNE_SERVER_MODE"] == "local" and not is_loopback_host(host):
        raise ValueError("Local-resource API must bind to loopback")
    app.run(host=host, port=port)
    return 0


__all__ = ["SessionRegistry", "create_app", "main"]
