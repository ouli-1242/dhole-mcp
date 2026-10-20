"""The distribution files must describe the build that exists, not a wish.

A registry manifest that names a version PyPI does not have, a compose file
that starts without a token, or an image whose entrypoint no longer matches the
CLI are the kind of distribution bugs that only surface after a stranger tries
to install. These checks are cheap and offline; they do not replace a real
`docker build` (that needs a daemon and is deliberately not in the default
suite).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_the_registry_manifest_names_the_version_this_tree_builds():
    from dhole_mcp import __version__

    data = json.loads(_text("server.json"))
    assert data["$schema"].endswith("/server.schema.json")
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", data["name"])
    assert data["version"] == __version__, "the manifest must track __version__"
    assert 1 <= len(data["description"]) <= 100
    assert data["repository"]["source"] == "github"
    packages = data["packages"]
    assert packages and all(p["registryType"] == "pypi" for p in packages)
    assert all(p["version"] == data["version"] for p in packages)
    assert all(p["transport"]["type"] == "stdio" for p in packages), (
        "the image defaults to HTTP, but the PyPI entry is the stdio install; "
        "a streamable-http transport here would describe a URL nobody runs")
    assert packages[0]["identifier"] == "dhole-mcp"


def test_the_image_is_non_root_and_serves_the_http_transport():
    dockerfile = _text("Dockerfile")
    assert "USER dhole" in dockerfile
    assert "EXPOSE 8765" in dockerfile
    assert "ENTRYPOINT [\"dhole\"]" in dockerfile
    assert '"--http", "--host", "0.0.0.0"' in dockerfile
    assert "DHOLE_HOME=/data" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "ARG INSTALL_BROWSER=1" in dockerfile, (
        "the default image must carry Chromium; INSTALL_BROWSER=0 is the opt-out")
    assert "patchright install --with-deps chromium" in dockerfile


def test_compose_refuses_to_start_without_a_token_and_keeps_loopback_by_default():
    compose = _text("docker-compose.yml")
    assert "${DHOLE_HTTP_TOKEN:?" in compose, \
        "an unset token must stop compose, not start an open fetcher"
    assert '"127.0.0.1:${DHOLE_PORT:-8765}:8765"' in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "read_only: true" in compose
    assert "INSTALL_BROWSER:-1" in compose, (
        "compose must default to the browser image, matching the Dockerfile")


def test_the_publish_workflow_only_pushes_on_a_version_tag():
    workflow = _text(".github/workflows/docker.yml")
    assert "tags: [\"v*\"]" in workflow
    assert "packages: write" in workflow
    assert "push: ${{ startsWith(github.ref, 'refs/tags/v') }}" in workflow
    assert "ghcr.io/${{ github.repository }}" in workflow


def test_the_readme_states_the_release_gate_and_tenant_boundary():
    readme = _text("README.md")
    assert "PyPI" in readme and "16.2" in readme, (
        "the tree is 17.0 while the index is 16.2 - distribution docs must say "
        "that the registry/pip path becomes live at release, not claim it now")
    assert "OAuth" in readme, "hosted mode has no OAuth; a bearer token/proxy is the boundary"
    assert "按主体隔离" in readme
