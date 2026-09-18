"""HTTP integration of the local/cloud boundary; no LFS fixtures required."""

from __future__ import annotations

import os
import secrets
from unittest.mock import Mock

import pytest

pytest.importorskip("flask")

from pne_scheduler.api import app as app_module  # noqa: E402
from pne_scheduler.api.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_security_environment(monkeypatch):
    for key in os.environ:
        if key.startswith("PNE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def local_client(tmp_path):
    return create_app(tmp_path / "library").test_client()


@pytest.fixture
def cloud_client(monkeypatch, tmp_path):
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("PNE_API_TOKEN", token)
    monkeypatch.setenv("PNE_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("PNE_ALLOWED_ORIGINS", "https://scheduler.example")
    # Cloud creation itself must not initialize server-local storage.
    monkeypatch.setattr(app_module, "MethodLibrary", Mock(side_effect=AssertionError("filesystem touched")))
    client = create_app(tmp_path / "library", mode="cloud").test_client()
    return client, {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("headers", [
    {"Host": "attacker.example"},
    {"Host": "localhost.attacker.example"},
    {"Host": "attacker.example", "X-Forwarded-Host": "localhost"},
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:9999"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_untrusted_browser_requests_are_rejected_before_routes(local_client, headers, monkeypatch):
    route = Mock(side_effect=AssertionError("route reached"))
    monkeypatch.setattr(app_module.routes, "import_open", route)
    response = local_client.post("/api/import/open", json={"path": "ignored"}, headers=headers)
    assert response.status_code == 403
    assert response.get_json()["ok"] is False
    route.assert_not_called()


def test_remote_peer_cannot_use_loopback_host_header(local_client):
    response = local_client.get("/api/health", environ_overrides={"REMOTE_ADDR": "192.0.2.1"})
    assert response.status_code == 403


def test_trusted_proxy_origin_works_without_forwarded_header_trust(local_client):
    response = local_client.get("/api/health", headers={"Origin": "http://localhost:3000",
                                                      "X-Forwarded-Host": "ignored.example"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "mode": "local", "localResources": True}


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/library"), ("POST", "/api/library"),
    ("GET", "/api/library/method"), ("POST", "/api/library/load"),
    ("POST", "/api/import/open"), ("POST", "/api/import/session/stage"),
    ("POST", "/api/import/session/clear"), ("POST", "/api/import/session/plan"),
    ("POST", "/api/export"), ("POST", "/api/export/"),
    ("OPTIONS", "/api/export"), ("POST", "/%61pi/export"),
])
def test_cloud_disables_every_filesystem_route_even_authenticated(cloud_client, method, path):
    client, headers = cloud_client
    response = client.open(path, method=method, headers=headers,
                           json={"path": "../../private.sch", "outDir": "/arbitrary"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "Local-resource routes are disabled"


def test_local_resource_flag_disables_local_routes(tmp_path):
    client = create_app(tmp_path / "library", local_resources=False).test_client()
    assert client.get("/api/library").status_code == 403
    assert client.post("/api/export", json={}).status_code == 403
    assert client.get("/api/health").get_json()["localResources"] is False


def test_cloud_token_required_and_never_returned(cloud_client):
    client, headers = cloud_client
    for supplied in ({}, {"Authorization": "Bearer invalid"}):
        assert client.get("/api/health", headers=supplied).status_code == 401
        assert client.post("/api/views", json={}, headers=supplied).status_code == 401
    response = client.get("/api/health", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "mode": "cloud", "localResources": False}
    assert headers["Authorization"] not in response.get_data(as_text=True)
    assert "libraryRoot" not in response.get_json()


def test_cloud_stateless_views_remain_available(cloud_client):
    client, headers = cloud_client
    project = {"schema": "pne_scheduler.schproj/v2", "name": "cloud draft",
               "sch_version": 0x00010003,
               "cell_profile": {"nominal_capacity_mAh": 80.0, "v_min": 2.5, "v_max": 4.2},
               "modules": [], "connections": []}
    response = client.post("/api/views", headers=headers, json={"project": project})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_cloud_rejects_untrusted_origin_even_with_token(cloud_client):
    client, headers = cloud_client
    response = client.get("/api/health", headers={**headers, "Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_errors_do_not_disclose_server_exception_text(local_client, monkeypatch):
    monkeypatch.setattr(app_module.routes, "views", Mock(side_effect=RuntimeError("private server detail")))
    response = local_client.post("/api/views", json={})
    assert response.status_code == 500
    assert "private server detail" not in response.get_data(as_text=True)


def test_local_launcher_rejects_public_bind(monkeypatch):
    monkeypatch.setattr(app_module, "create_app", lambda: Mock(config={"PNE_SERVER_MODE": "local"}))
    with pytest.raises(ValueError, match="loopback"):
        app_module.main(host="0.0.0.0")