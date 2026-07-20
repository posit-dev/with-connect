import os
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, Mock

import docker
import main


def test_license_file_not_exists():
    result = subprocess.run(
        [sys.executable, "main.py", "--license", "/nonexistent/path/license.lic"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Error: License file does not exist:" in result.stderr
    assert "/nonexistent/path/license.lic" in result.stderr


def test_config_file_not_exists():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lic", delete=False) as f:
        license_file = f.name

    try:
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--license",
                license_file,
                "--config",
                "/nonexistent/path/config.gcfg",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "Error: Config file does not exist:" in result.stderr
        assert "/nonexistent/path/config.gcfg" in result.stderr
    finally:
        os.unlink(license_file)


def test_license_file_with_tilde_expansion():
    result = subprocess.run(
        [sys.executable, "main.py", "--license", "~/nonexistent-license.lic"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Error: License file does not exist:" in result.stderr
    home_path = os.path.expanduser("~")
    assert home_path in result.stderr


def test_invalid_license_detection():
    mock_container = MagicMock()
    mock_container.logs.return_value = b'time="2025-11-06T13:05:18.790Z" level=warning msg="Unable to obtain a valid license: Your Posit Connect license has expired."'

    try:
        main.wait_for_http_server(mock_container, timeout=1.0, poll_interval=0.1)
        assert False, "Expected RuntimeError to be raised"
    except RuntimeError as e:
        assert "Unable to obtain a valid license" in str(e)
        assert "expired or invalid" in str(e)
        mock_container.stop.assert_called_once()


def test_valid_license_http_server_starts():
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Starting HTTP server on [::]:3939"

    result = main.wait_for_http_server(mock_container, timeout=1.0, poll_interval=0.1)

    assert result is True
    mock_container.stop.assert_not_called()


def test_get_docker_tag_latest():
    assert main.get_docker_tag("latest") == ("ghcr.io/posit-dev/connect", "latest")


def test_get_docker_tag_release():
    assert main.get_docker_tag("release") == ("ghcr.io/posit-dev/connect", "latest")


def test_get_docker_tag_preview():
    assert main.get_docker_tag("preview") == ("ghcr.io/posit-dev/connect-preview", "daily")


def test_get_docker_tag_ghcr_version():
    # 2026.04 and later: ghcr.io/posit-dev/connect with bare version
    assert main.get_docker_tag("2026.04.0") == ("ghcr.io/posit-dev/connect", "2026.04.0")
    assert main.get_docker_tag("2026.05.1") == ("ghcr.io/posit-dev/connect", "2026.05.1")
    assert main.get_docker_tag("2027.01.0") == ("ghcr.io/posit-dev/connect", "2027.01.0")


def test_get_docker_tag_jammy_version():
    # Pre-cutover jammy era (2023.07 - 2026.03): legacy Docker Hub jammy- prefix
    assert main.get_docker_tag("2026.03.0") == ("rstudio/rstudio-connect", "jammy-2026.03.0")
    assert main.get_docker_tag("2025.09.0") == ("rstudio/rstudio-connect", "jammy-2025.09.0")
    assert main.get_docker_tag("2024.01.0") == ("rstudio/rstudio-connect", "jammy-2024.01.0")
    assert main.get_docker_tag("2023.07.0") == ("rstudio/rstudio-connect", "jammy-2023.07.0")


def test_get_docker_tag_bionic_version():
    assert main.get_docker_tag("2023.06.0") == ("rstudio/rstudio-connect", "bionic-2023.06.0")
    assert main.get_docker_tag("2023.01.0") == ("rstudio/rstudio-connect", "bionic-2023.01.0")
    assert main.get_docker_tag("2022.09.0") == ("rstudio/rstudio-connect", "bionic-2022.09.0")


def test_get_docker_tag_old_version():
    assert main.get_docker_tag("2022.08.0") == ("rstudio/rstudio-connect", "2022.08.0")
    assert main.get_docker_tag("2021.12.0") == ("rstudio/rstudio-connect", "2021.12.0")


def test_get_docker_tag_invalid_format():
    assert main.get_docker_tag("jammy") == ("rstudio/rstudio-connect", "jammy")
    assert main.get_docker_tag("custom-tag") == ("rstudio/rstudio-connect", "custom-tag")


def test_force_amd64_legacy_rstudio_images():
    # Legacy rstudio/* on Docker Hub and ghcr.io/rstudio/* mirrors are
    # amd64-only and need the pin.
    assert main._force_amd64("rstudio/rstudio-connect") is True
    assert main._force_amd64("rstudio/rstudio-connect-preview") is True
    assert main._force_amd64("ghcr.io/rstudio/rstudio-connect") is True
    assert main._force_amd64("ghcr.io/rstudio/rstudio-connect-preview") is True


def test_force_amd64_modern_images():
    # Modern ghcr.io/posit-dev/* and posit/* images ship multi-arch
    # manifests.
    assert main._force_amd64("ghcr.io/posit-dev/connect") is False
    assert main._force_amd64("ghcr.io/posit-dev/connect-preview") is False
    assert main._force_amd64("posit/connect") is False
    assert main._force_amd64("posit/connect-preview") is False


def test_extract_server_version():
    logs = 'time="2025-11-06T13:05:18.626Z" level=info msg="Starting Posit Connect v2025.09.0"'
    assert main.extract_server_version(logs) == "2025.09.0"


def test_extract_server_version_multiple_lines():
    logs = '''time="2025-11-06T13:05:18.626Z" level=info msg="Starting Posit Connect v2024.08.0"
time="2025-11-06T13:05:18.790Z" level=info msg="Starting HTTP server on [::]:3939"'''
    assert main.extract_server_version(logs) == "2024.08.0"


def test_extract_server_version_not_found():
    logs = 'time="2025-11-06T13:05:18.626Z" level=info msg="Some other message"'
    assert main.extract_server_version(logs) is None


def test_extract_server_version_dev():
    logs = 'time="2025-11-06T13:05:18.626Z" level=info msg="Starting Posit Connect v2025.11.0-dev+29-gd0db52662c"'
    assert main.extract_server_version(logs) == "2025.11.0-dev+29-gd0db52662c"


def test_local_image_usage():
    mock_args = Mock()
    mock_args.version = "2024.08.0"
    mock_args.license = "test.lic"
    mock_args.config = None
    mock_args.quiet = False

    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_client.images.get.return_value = mock_image

    base_image, tag = main.get_docker_tag(mock_args.version)
    image_name = f"{base_image}:{tag}"

    try:
        mock_client.images.get(image_name)
        should_pull = False
    except docker.errors.ImageNotFound:
        should_pull = True

    assert should_pull is False


def test_release_always_pulls():
    mock_args = Mock()
    mock_args.version = "release"

    should_pull = mock_args.version in ("latest", "release", "preview")
    assert should_pull is True


def test_preview_always_pulls():
    mock_args = Mock()
    mock_args.version = "preview"

    should_pull = mock_args.version in ("latest", "release", "preview")
    assert should_pull is True


def test_custom_port():
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )

    assert "--port" in result.stdout
    assert "default: 3939" in result.stdout


def test_image_and_version_exclusive():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lic", delete=False) as f:
        license_file = f.name

    try:
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--license",
                license_file,
                "--image",
                "rstudio/rstudio-connect:jammy-2025.09.0",
                "--version",
                "2024.08.0",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert "Cannot specify both 'image' and 'version'" in result.stderr
    finally:
        os.unlink(license_file)


def test_image_without_tag():
    base_image, tag, used_default = main.parse_image_spec("rstudio/rstudio-connect")
    assert base_image == "rstudio/rstudio-connect"
    assert tag == "latest"
    assert used_default is True


def test_image_with_tag():
    base_image, tag, used_default = main.parse_image_spec("rstudio/rstudio-connect:jammy-2025.09.0")
    assert base_image == "rstudio/rstudio-connect"
    assert tag == "jammy-2025.09.0"
    assert used_default is False


def test_stop_argument_in_help():
    """Test that --stop argument is available."""
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )

    assert "--stop" in result.stdout
    assert "CONTAINER_ID" in result.stdout


def test_stop_nonexistent_container():
    """Test that --stop with nonexistent container returns error."""
    result = subprocess.run(
        [sys.executable, "main.py", "--stop", "nonexistent_container_id"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Container not found" in result.stderr


def test_reset_argument_in_help():
    """Test that --reset argument is available."""
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )

    assert "--reset" in result.stdout
    assert "CONTAINER_ID" in result.stdout


def test_reset_nonexistent_container():
    """Test that --reset with nonexistent container returns error."""
    result = subprocess.run(
        [sys.executable, "main.py", "--reset", "nonexistent_container_id"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Container not found" in result.stderr


def test_get_connect_pid_found():
    """Returns the Connect worker PID, not the PID 1 keep-alive that shares the
    process table with it. stop_connect kills whatever this returns, so picking
    the wrong line (e.g. PID 1) would stop the container."""
    c = MagicMock()
    c.exec_run.return_value = (
        0,
        b"    PID COMMAND\n      1 sleep infinity\n     20 /opt/rstudio-connect/bin/connect --config /etc/rstudio-connect/rstudio-connect.gcfg\n",
    )
    assert main.get_connect_pid(c) == 20


def test_get_connect_pid_not_found():
    """When only the keep-alive (sleep infinity, PID 1) is running and Connect is
    not, returns None so stop_connect safely no-ops instead of targeting PID 1."""
    c = MagicMock()
    c.exec_run.return_value = (0, b"    PID COMMAND\n      1 sleep infinity\n")
    assert main.get_connect_pid(c) is None


def test_get_connect_pid_raises_on_ps_failure():
    c = MagicMock()
    c.exec_run.return_value = (127, b"sh: ps: not found")
    try:
        main.get_connect_pid(c)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "ps exited" in str(e).lower() or "could not list processes" in str(e).lower()


def test_stop_connect_graceful():
    c = MagicMock()
    c.exec_run.side_effect = [
        (0, b"     20 /opt/rstudio-connect/bin/connect --config x"),  # ps
        (0, b""),  # kill -TERM
        (1, b""),  # kill -0 -> gone
    ]
    main.stop_connect(c, timeout=5, poll_interval=0.01)
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    assert ["kill", "-TERM", "20"] in cmds
    assert ["kill", "-KILL", "20"] not in cmds


def test_stop_connect_force_kill_on_timeout():
    c = MagicMock()
    seq = [
        (0, b"     20 /opt/rstudio-connect/bin/connect --config x"),  # ps
        (0, b""),  # kill -TERM
    ]

    def fake_exec(cmd, **kwargs):
        if seq:
            return seq.pop(0)
        return (0, b"")  # kill -0 always alive; then kill -KILL

    c.exec_run.side_effect = fake_exec
    main.stop_connect(c, timeout=0.05, poll_interval=0.01)
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    assert ["kill", "-KILL", "20"] in cmds


def test_stop_connect_never_kills_pid_1():
    """Safety: stop_connect must never signal PID 1 (the keep-alive), even if the
    process table reports Connect as PID 1 -- killing it stops the container and
    defeats reset-in-place."""
    c = MagicMock()
    c.exec_run.return_value = (
        0,
        b"    PID COMMAND\n      1 /opt/rstudio-connect/bin/connect --config x\n",
    )
    main.stop_connect(c, timeout=0.05, poll_interval=0.01)
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    assert not any(cmd[:2] == ["kill", "-TERM"] for cmd in cmds), "must not SIGTERM PID 1"
    assert not any(cmd[:2] == ["kill", "-KILL"] for cmd in cmds), "must not SIGKILL PID 1"


def test_discover_host_port():
    c = MagicMock()
    c.attrs = {
        "NetworkSettings": {"Ports": {"3939/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3941"}]}}
    }
    assert main.discover_host_port(c) == 3941
    c.reload.assert_called_once()


def test_discover_host_port_missing():
    c = MagicMock()
    c.attrs = {"NetworkSettings": {"Ports": {}}}
    try:
        main.discover_host_port(c)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "3939/tcp" in str(e)


def test_restore_baseline_wipes_and_extracts():
    c = MagicMock()
    c.exec_run.return_value = (0, b"")
    main.restore_baseline(c, main.DATA_DIR)
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    # First: bash wipe that deletes everything except the license bind-mount
    assert cmds[0][0] == "bash"
    assert main.DATA_DIR in cmds[0][2]
    assert f"-not -path './{main.LICENSE_FILENAME}'" in cmds[0][2]
    assert "-delete" in cmds[0][2]
    # Second: extract the baseline archive into the data dir
    assert cmds[1][:3] == ["tar", "xzf", main.BASELINE_PATH]
    assert cmds[1][-1] == main.DATA_DIR


def test_restore_baseline_targets_resolved_data_dir():
    """The wipe and extract operate on the data dir passed in, not a hard-coded
    default -- so a legacy image's /data is reset, not the empty default dir."""
    c = MagicMock()
    c.exec_run.return_value = (0, b"")
    main.restore_baseline(c, "/data")
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    assert "/data" in cmds[0][2] and "-delete" in cmds[0][2]
    assert cmds[1][-1] == "/data"


def test_restore_baseline_raises_on_tar_failure():
    c = MagicMock()
    c.exec_run.side_effect = [(0, b""), (2, b"tar: broken")]
    try:
        main.restore_baseline(c, main.DATA_DIR)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "restore baseline" in str(e).lower()


def test_restore_baseline_raises_on_wipe_failure():
    c = MagicMock()
    c.exec_run.side_effect = [(1, b"find: permission denied")]  # wipe fails first
    try:
        main.restore_baseline(c, main.DATA_DIR)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "wipe data dir" in str(e).lower()


def test_get_connect_launch_command_cmd_only():
    c = MagicMock()
    c.image.attrs = {"Config": {"Entrypoint": None, "Cmd": ["/usr/local/bin/startup.sh"]}}
    assert main.get_connect_launch_command(c) == ["/usr/local/bin/startup.sh"]


def test_get_connect_launch_command_entrypoint_and_cmd():
    c = MagicMock()
    c.image.attrs = {"Config": {"Entrypoint": ["/tini", "--"], "Cmd": ["connect", "serve"]}}
    assert main.get_connect_launch_command(c) == ["/tini", "--", "connect", "serve"]


def test_get_connect_launch_command_empty_raises():
    c = MagicMock()
    c.image.attrs = {"Config": {"Entrypoint": None, "Cmd": None}}
    try:
        main.get_connect_launch_command(c)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "launch command" in str(e).lower()


def test_start_connect_uses_image_command_and_routes_logs():
    """start_connect runs the image's own launch command (Entrypoint + Cmd) as a
    detached child, routing output to PID 1 so container.logs() keeps working."""
    c = MagicMock()
    c.image.attrs = {"Config": {"Entrypoint": ["tini", "--"], "Cmd": ["/usr/local/bin/startup.sh"]}}
    main.start_connect(c)
    cmd = c.exec_run.call_args.args[0]
    assert cmd[0] == "bash"
    assert "tini -- /usr/local/bin/startup.sh" in cmd[2]
    assert "/proc/1/fd/1" in cmd[2]
    assert c.exec_run.call_args.kwargs.get("detach") is True


def test_capture_baseline_snapshots_excluding_license():
    c = MagicMock()
    c.exec_run.return_value = (0, b"")
    c.image.attrs = {"Config": {"Entrypoint": None, "Cmd": ["/usr/local/bin/startup.sh"]}}
    main.capture_baseline(c, main.DATA_DIR)
    cmds = [call.args[0] for call in c.exec_run.call_args_list]
    tar_cmds = [cmd for cmd in cmds if cmd and cmd[0] == "tar" and "czf" in cmd]
    assert tar_cmds, "expected a tar czf command"
    tar = tar_cmds[0]
    assert main.BASELINE_PATH in tar
    # The snapshot is taken from the resolved data dir (-C <data_dir>).
    assert tar[tar.index("-C") + 1] == main.DATA_DIR
    # The license bind-mount must be excluded from the snapshot, or tar would
    # read the license through the mount into the archive.
    assert f"--exclude=./{main.LICENSE_FILENAME}" in tar


def test_capture_baseline_raises_on_tar_failure():
    c = MagicMock()
    c.image.attrs = {"Config": {"Entrypoint": None, "Cmd": ["/usr/local/bin/startup.sh"]}}
    # stop_connect ps (no connect), mkdir, then tar czf fails
    c.exec_run.side_effect = [(0, b""), (0, b""), (2, b"tar: broken")]
    try:
        main.capture_baseline(c, main.DATA_DIR)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "capture baseline" in str(e).lower()


def test_get_data_dir_uses_most_recent_log_line():
    """Resolves Connect's effective data dir from its startup log, taking the
    latest line (a container accumulates one per boot / reset)."""
    c = MagicMock()
    c.logs.return_value = (
        b'time="..." level=info msg="Using data directory: /var/lib/rstudio-connect"\n'
        b'time="..." level=info msg="Using data directory: /data"\n'
    )
    assert main.get_data_dir(c) == "/data"


def test_get_data_dir_falls_back_to_default():
    c = MagicMock()
    c.logs.return_value = b"no data directory line logged here"
    assert main.get_data_dir(c) == main.DATA_DIR


def test_get_data_dir_matches_creating_on_first_boot():
    """A directory that doesn't already exist on disk (e.g. a custom
    Server.DataDir/CONNECT_SERVER_DATADIR pointed at a fresh path) logs
    "Creating data directory: <path>" on its first boot, not "Using" -- the
    built-in default dirs only log "Using" because they're pre-created in the
    image. Missing this would silently fall back to DATA_DIR and capture an
    empty baseline from the wrong directory."""
    c = MagicMock()
    c.logs.return_value = (
        b'time="..." level=info msg="Creating data directory: /custom-data-dir"\n'
    )
    assert main.get_data_dir(c) == "/custom-data-dir"


def test_resolve_real_path_follows_symlink():
    c = MagicMock()
    c.exec_run.return_value = (0, b"/var/lib/with-connect\n")
    assert main.resolve_real_path(c, "/mydata") == "/var/lib/with-connect"
    c.exec_run.assert_called_once_with(["readlink", "-f", "/mydata"], user="root")


def test_resolve_real_path_raises_on_failure():
    c = MagicMock()
    c.exec_run.return_value = (1, b"readlink: /mydata: No such file or directory")
    try:
        main.resolve_real_path(c, "/mydata")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "resolve real path" in str(e).lower()


def test_stop_container_graceful_for_start_only():
    """A start-only container (has a baseline) is stopped gracefully: Connect
    first, then a short container-stop timeout instead of the full ~10s grace."""
    from unittest.mock import patch

    c = MagicMock()
    c.status = "running"
    c.exec_run.return_value = (0, b"")  # has_baseline True; ps finds no connect
    client = MagicMock()
    client.containers.get.return_value = c
    with patch.object(main.docker, "from_env", return_value=client):
        main.stop_container("abc123")
    c.stop.assert_called_once_with(timeout=2)


def test_stop_container_stops_even_if_connect_stop_fails():
    """The graceful Connect stop is best-effort. If it fails (e.g. a custom image
    without ps, so get_connect_pid raises), --stop must still stop the container
    instead of erroring out and leaving it running."""
    from unittest.mock import patch

    c = MagicMock()
    c.status = "running"
    # has_baseline -> (0) True; then stop_connect's ps -> non-zero (raises)
    c.exec_run.side_effect = [(0, b""), (127, b"sh: ps: not found")]
    client = MagicMock()
    client.containers.get.return_value = c
    with patch.object(main.docker, "from_env", return_value=client):
        main.stop_container("abc123")
    c.stop.assert_called_once_with(timeout=2)


def test_stop_container_plain_for_unmanaged():
    """A container without a baseline uses a plain container.stop()."""
    from unittest.mock import patch

    c = MagicMock()
    c.status = "running"
    c.exec_run.return_value = (1, b"")  # has_baseline False
    client = MagicMock()
    client.containers.get.return_value = c
    with patch.object(main.docker, "from_env", return_value=client):
        main.stop_container("abc123")
    c.stop.assert_called_once_with()


def test_reset_container_no_baseline_raises():
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.exec_run.return_value = (1, b"")  # has_baseline -> False
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "start-only mode" in str(e)


def test_reset_container_external_db_raises():
    """Reset must fail loudly (not silently no-op) when the resolved data dir has
    no built-in SQLite database -- i.e. Connect points at an external database."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /var/lib/rstudio-connect"'

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:  # resolve_real_path -> echo input unchanged
            return (0, (cmd[2] + "\n").encode())
        if "baseline.tgz" in joined:   # has_baseline -> True
            return (0, b"")
        if "/db/" in joined:           # data_dir_has_sqlite_db -> False (external DB)
            return (2, b"ls: no such file or directory")
        return (0, b"")

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "external database" in str(e).lower()
    # It must bail before mutating: the data-dir wipe (restore_baseline) never ran.
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    assert not any("-delete" in c for c in cmds), "must not wipe the data dir when the guard fires"


def test_data_dir_contains_baseline_dir():
    assert main.data_dir_contains_baseline_dir(main.BASELINE_DIR) is True
    assert main.data_dir_contains_baseline_dir("/var/lib") is True
    # The root dir is an ancestor of everything, including BASELINE_DIR -- a
    # naive `baseline.startswith(data + "/")` check turns this into
    # `startswith("//")`, which never matches, so this case regressed once.
    assert main.data_dir_contains_baseline_dir("/") is True
    assert main.data_dir_contains_baseline_dir(main.DATA_DIR) is False
    assert main.data_dir_contains_baseline_dir("/data") is False
    # Must not falsely match on a raw string prefix without a path separator:
    # "/var/lib/with-connect-other" is a sibling, not an ancestor of BASELINE_DIR.
    assert main.data_dir_contains_baseline_dir(main.BASELINE_DIR + "-other") is False


def test_data_dir_overlaps_baseline_checks_literal_and_resolved():
    """data_dir_overlaps_baseline must catch an overlap via EITHER BASELINE_DIR's
    literal path or its resolved real target, since either one being deleted
    is unsafe (the literal path breaks our own subsequent references; the
    resolved target is where the archive's actual bytes live)."""
    def readlink_returns(target):
        c = MagicMock()
        c.exec_run.return_value = (0, (target + "\n").encode())
        return c

    # Resolved target overlaps data_dir, even though the literal path doesn't.
    assert main.data_dir_overlaps_baseline(readlink_returns("/data/with-connect"), "/data") is True
    # Literal path overlaps data_dir, even though the resolved target doesn't.
    assert main.data_dir_overlaps_baseline(readlink_returns("/opt/with-connect"), "/var/lib") is True
    # Neither overlaps.
    assert main.data_dir_overlaps_baseline(readlink_returns(main.BASELINE_DIR), "/data") is False


def test_reset_container_baseline_dir_overlap_raises():
    """Reset must fail loudly (not silently no-op, and not wipe anything) when
    the resolved data dir is or contains BASELINE_DIR -- e.g. a custom
    Server.DataDir set to /var/lib -- since the wipe would delete the baseline
    archive the restore step needs, along with anything else under it."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /var/lib"'

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:  # resolve_real_path -> echo input unchanged
            return (0, (cmd[2] + "\n").encode())
        if "baseline.tgz" in joined:  # has_baseline -> True
            return (0, b"")
        if "/db/" in joined:          # data_dir_has_sqlite_db -> True
            return (0, b"/var/lib/db/connect.db\n")
        return (0, b"")

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert main.BASELINE_DIR in str(e)
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    assert not any("-delete" in c for c in cmds), "must not wipe the data dir when the guard fires"


def test_reset_container_symlinked_datadir_overlap_raises():
    """The overlap guard must catch a Server.DataDir that's a symlink into
    BASELINE_DIR, not just a lexical match -- resolve_real_path follows the
    symlink before data_dir_contains_baseline_dir compares paths."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /mydata"'

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:  # /mydata is a symlink into BASELINE_DIR
            return (0, b"/var/lib/with-connect\n")
        if "baseline.tgz" in joined:  # has_baseline -> True
            return (0, b"")
        if "/db/" in joined:          # data_dir_has_sqlite_db -> True
            return (0, b"/var/lib/with-connect/db/connect.db\n")
        return (0, b"")

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert main.BASELINE_DIR in str(e)
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    assert not any("-delete" in c for c in cmds), "must not wipe the data dir when the guard fires"


def test_reset_container_symlinked_baseline_dir_overlap_raises():
    """The overlap guard must also resolve BASELINE_DIR itself, not just
    data_dir -- if BASELINE_DIR is a symlink (e.g. because /var/lib is
    symlinked on a read-only-root image), comparing data_dir against the
    unresolved BASELINE_DIR constant would miss an overlap that only exists
    through the symlink's real target."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /data"'

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:
            if cmd[2] == main.BASELINE_DIR:
                # BASELINE_DIR is a symlink whose real target lives under /data.
                return (0, b"/data/with-connect\n")
            return (0, (cmd[2] + "\n").encode())  # data_dir itself isn't a symlink
        if "baseline.tgz" in joined:  # has_baseline -> True
            return (0, b"")
        if "/db/" in joined:          # data_dir_has_sqlite_db -> True
            return (0, b"/data/db/connect.db\n")
        return (0, b"")

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert main.BASELINE_DIR in str(e)
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    assert not any("-delete" in c for c in cmds), "must not wipe the data dir when the guard fires"


def test_reset_container_literal_baseline_dir_overlap_raises():
    """The overlap guard must also catch data_dir overlapping BASELINE_DIR's
    LITERAL path even when BASELINE_DIR resolves elsewhere -- e.g. BASELINE_DIR
    is itself a symlink pointing outside data_dir. Wiping data_dir would still
    delete that symlink's directory entry (not its target's contents), which
    breaks every subsequent reference built from the literal BASELINE_PATH
    constant, even though the archive's real bytes survive. A resolved-only
    comparison would miss this, since the resolved target doesn't overlap."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /var/lib"'

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:
            if cmd[2] == main.BASELINE_DIR:
                # BASELINE_DIR is a symlink resolving OUTSIDE data_dir.
                return (0, b"/opt/with-connect\n")
            return (0, (cmd[2] + "\n").encode())  # data_dir itself isn't a symlink
        if "baseline.tgz" in joined:  # has_baseline -> True
            return (0, b"")
        if "/db/" in joined:          # data_dir_has_sqlite_db -> True
            return (0, b"/var/lib/db/connect.db\n")
        return (0, b"")

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert main.BASELINE_DIR in str(e)
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    assert not any("-delete" in c for c in cmds), "must not wipe the data dir when the guard fires"


def test_reset_container_resets_resolved_data_dir():
    """Reset resolves the effective data dir (e.g. the legacy image's /data) and
    wipes/restores THAT dir, not the hard-coded default."""
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_container.logs.return_value = b'msg="Using data directory: /data"'
    mock_container.image.attrs = {
        "Config": {"Entrypoint": None, "Cmd": ["/usr/local/bin/startup.sh"]}
    }

    def fake_exec(cmd, **kwargs):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if cmd[:2] == ["readlink", "-f"]:  # resolve_real_path -> echo input unchanged
            return (0, (cmd[2] + "\n").encode())
        if "test -f" in joined and "baseline.tgz" in joined:  # has_baseline -> True
            return (0, b"")
        if "/db/*.db" in joined:                              # has SQLite db -> True
            return (0, b"/data/db/connect.db\n")
        if "ps -eo" in joined:                                # get_connect_pid -> none running
            return (0, b"  PID ARGS\n1 sleep infinity\n")
        return (0, b"")  # wipe, tar restore, start_connect all succeed

    mock_container.exec_run.side_effect = fake_exec
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client), \
            patch.object(main, "discover_host_port", return_value=3939), \
            patch.object(main, "wait_until_healthy", return_value=True):
        main.reset_container("abc123")
    cmds = [
        " ".join(c.args[0]) if isinstance(c.args[0], list) else str(c.args[0])
        for c in mock_container.exec_run.call_args_list
    ]
    # The wipe targets /data, and the baseline is extracted back into /data.
    assert any("/data" in c and "-delete" in c for c in cmds), cmds
    assert any("-C /data" in c for c in cmds), cmds


def test_reset_container_not_running_raises():
    from unittest.mock import patch

    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    with patch.object(main.docker, "from_env", return_value=mock_client):
        try:
            main.reset_container("abc123")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "not running" in str(e)


def test_start_only_run_kwargs_have_healthcheck_and_init():
    """Start-only containers must get the keep-alive command, the crash-surfacing
    healthcheck, and init=True so docker-init reaps orphaned Connect processes."""
    kwargs = main.build_run_kwargs(
        "img:tag", 3939, [], {}, "ghcr.io/posit-dev/connect", is_start_only=True
    )
    assert kwargs["command"] == main.KEEPALIVE_CMD
    assert kwargs["healthcheck"] == main.HEALTHCHECK
    assert kwargs["init"] is True


def test_command_mode_run_kwargs_omit_healthcheck_and_init():
    """Command mode uses the image's default entrypoint: no keep-alive command, no
    injected healthcheck, no init override."""
    kwargs = main.build_run_kwargs(
        "img:tag", 3939, [], {}, "ghcr.io/posit-dev/connect", is_start_only=False
    )
    assert "command" not in kwargs
    assert "healthcheck" not in kwargs
    assert "init" not in kwargs


def test_cli_reports_runtime_error_without_traceback():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['with-connect','--reset','nonexistent_cli_test']; "
         "import main; main.cli()"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Error: Container not found" in result.stderr
    assert "Traceback" not in result.stderr


if __name__ == "__main__":
    _failures = 0
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"✓ {_name} passed")
            except Exception as _e:  # noqa: BLE001
                _failures += 1
                print(f"✗ {_name} FAILED: {_e!r}")
    if _failures:
        print(f"\n{_failures} test(s) failed!")
        sys.exit(1)
    print("\nAll tests passed!")
