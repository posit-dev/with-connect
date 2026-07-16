"""End-to-end test for --reset against a real Connect container.

Skipped unless CONNECT_LICENSE_FILE points to a valid license file and Docker is
available. CONNECT_LICENSE_FILE is the same env var the Connect repo uses for a
license path, so this "just works" for Connect developers. Run:

    CONNECT_LICENSE_FILE=/path/to/license.lic \
      env -u PYENV_VERSION uv run --with pytest pytest test_integration.py -v
"""
import json
import os
import subprocess
import sys
import urllib.request

import pytest

LICENSE = os.environ.get("CONNECT_LICENSE_FILE")
PORT = 3951

pytestmark = pytest.mark.skipif(
    not (LICENSE and os.path.exists(LICENSE)),
    reason="set CONNECT_LICENSE_FILE to a valid license path to run",
)


def _api(server, path, key, method="GET", body=None):
    req = urllib.request.Request(
        server + path,
        method=method,
        headers={"Authorization": f"Key {key}"},
    )
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else None)


def test_reset_end_to_end():
    import docker

    client = docker.from_env()

    start = subprocess.run(
        [sys.executable, "main.py", "--license", LICENSE, "--port", str(PORT)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert start.returncode == 0, start.stderr
    kv = dict(
        line.split("=", 1)
        for line in start.stdout.strip().splitlines()
        if "=" in line
    )
    key, server, cid = kv["CONNECT_API_KEY"], kv["CONNECT_SERVER"], kv["CONTAINER_ID"]

    try:
        # Clean to start.
        status, content = _api(server, "/__api__/v1/content", key)
        assert status == 200 and content == []

        # Dirty it.
        status, _ = _api(
            server, "/__api__/v1/content", key, "POST",
            {"name": "itest-content", "title": "itest"},
        )
        assert status == 200
        _, content = _api(server, "/__api__/v1/content", key)
        assert len(content) == 1

        # Reset.
        reset = subprocess.run(
            [sys.executable, "main.py", "--reset", cid],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert reset.returncode == 0, reset.stderr

        # Same key still authenticates; content is gone; container still running.
        status, content = _api(server, "/__api__/v1/content", key)
        assert status == 200, "same API key should still work after reset"
        assert content == [], "content should be wiped after reset"
        client.containers.get(cid).reload()
        assert client.containers.get(cid).status == "running"
    finally:
        subprocess.run(
            [sys.executable, "main.py", "--stop", cid],
            capture_output=True,
            text=True,
        )
