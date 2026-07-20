"""End-to-end tests for --reset against a real Connect container.

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
import time
import urllib.request

import pytest

LICENSE = os.environ.get("CONNECT_LICENSE_FILE")
PORT = 3951
EXTERNAL_DB_PORT = 3952
CUSTOM_DATADIR_PORT = 3953

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


def test_reset_refuses_external_database():
    """--reset must refuse (not silently no-op) when Connect points at an
    external Postgres database instead of its built-in SQLite database."""
    import docker

    client = docker.from_env()
    pg = client.containers.run(
        "postgres:17.0",
        detach=True,
        environment={
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "password",
            "POSTGRES_DB": "connect",
        },
    )
    try:
        # Run via `exec` (inside the container), not a host-side TCP check:
        # Docker Desktop's containers live inside its VM, so a container's
        # bridge IP is reachable from OTHER containers but not from the host
        # process itself -- a host-side check would never succeed there.
        #
        # `-h 127.0.0.1 -d connect` (rather than bare `pg_isready -U admin`)
        # forces checking the real TCP listener against the actual target
        # database: postgres's own init runs a temporary, unix-socket-only
        # server (explicitly configured with no TCP listener) to execute init
        # scripts before the real server binds 0.0.0.0:5432, so an unqualified
        # `pg_isready` risks reporting ready against that temp server; `-d
        # connect` also avoids spurious "database admin does not exist"
        # errors from pg_isready's default-to-username database.
        for _ in range(30):
            code, _ = pg.exec_run(["pg_isready", "-h", "127.0.0.1", "-U", "admin", "-d", "connect"])
            if code == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres did not become ready in time")

        # IP-based, not hostname-based: works whether the runner is Docker
        # Desktop (macOS) or a plain Linux Docker Engine (CI), since both put
        # unrelated containers on the same default bridge network by default.
        pg.reload()
        pg_ip = pg.attrs["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
        pg_url = f"postgres://admin:password@{pg_ip}:5432/connect?sslmode=disable"

        start = subprocess.run(
            [
                sys.executable, "main.py",
                "--license", LICENSE,
                "--port", str(EXTERNAL_DB_PORT),
                "--env", "CONNECT_DATABASE_PROVIDER=postgres",
                "--env", f"CONNECT_POSTGRES_URL={pg_url}",
            ],
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
        cid = kv["CONTAINER_ID"]

        try:
            reset = subprocess.run(
                [sys.executable, "main.py", "--reset", cid],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert reset.returncode != 0, "reset should refuse against an external database"
            assert "external database" in reset.stderr.lower(), reset.stderr

            client.containers.get(cid).reload()
            assert client.containers.get(cid).status == "running", (
                "container must be left running, untouched, after refusing to reset"
            )
        finally:
            subprocess.run(
                [sys.executable, "main.py", "--stop", cid],
                capture_output=True,
                text=True,
            )
    finally:
        pg.stop()
        pg.remove()


def test_reset_works_with_custom_datadir():
    """--reset must work against a custom Server.DataDir path that doesn't
    already exist in the image. Connect logs "Creating data directory" (not
    "Using") the first time such a path is created, unlike the two built-in
    default dirs, which are pre-created at image-build time and always log
    "Using" even on a fresh container -- get_data_dir must resolve this
    correctly or capture_baseline silently snapshots the wrong, empty
    directory, and a later --reset destroys real data instead of restoring it."""
    start = subprocess.run(
        [
            sys.executable, "main.py",
            "--license", LICENSE,
            "--port", str(CUSTOM_DATADIR_PORT),
            "--env", "CONNECT_SERVER_DATADIR=/custom-data-dir",
        ],
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
        status, content = _api(server, "/__api__/v1/content", key)
        assert status == 200 and content == []

        status, _ = _api(
            server, "/__api__/v1/content", key, "POST",
            {"name": "itest-custom-datadir", "title": "itest"},
        )
        assert status == 200
        _, content = _api(server, "/__api__/v1/content", key)
        assert len(content) == 1

        reset = subprocess.run(
            [sys.executable, "main.py", "--reset", cid],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert reset.returncode == 0, reset.stderr

        status, content = _api(server, "/__api__/v1/content", key)
        assert status == 200, "same API key should still work after reset"
        assert content == [], "content should be wiped after reset"
    finally:
        subprocess.run(
            [sys.executable, "main.py", "--stop", cid],
            capture_output=True,
            text=True,
        )
