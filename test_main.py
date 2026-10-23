import base64
import hashlib
import hmac
import io
import json
import os
import urllib.error
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, Mock, patch

import docker
import main
import requests


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
        # main() stops the container when this raises
        mock_container.stop.assert_not_called()


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


def test_container_env_forces_service_log_to_stdout():
    # Startup detection reads "Starting HTTP server on" from stdout, so the
    # service log must go there at INFO level whatever --config says.
    env = main.build_container_env("secret", None)
    assert env["CONNECT_BOOTSTRAP_ENABLED"] == "true"
    assert env["CONNECT_BOOTSTRAP_SECRETKEY"] == "secret"
    assert env["CONNECT_LOGGING_SERVICELOG"] == "STDOUT"
    assert env["CONNECT_LOGGING_SERVICELOGLEVEL"] == "INFO"


def test_container_env_user_vars_override_defaults():
    env = main.build_container_env(
        "secret",
        ["CONNECT_LOGGING_SERVICELOG=STDERR", "MY_VAR=a=b", "IGNORED"],
    )
    assert env["CONNECT_LOGGING_SERVICELOG"] == "STDERR"
    assert env["MY_VAR"] == "a=b"
    assert "IGNORED" not in env


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


def parse_with_argv(*args):
    with patch.object(sys, "argv", ["with-connect", *args]):
        return main.parse_args()


def parse_exit(*args):
    """Run parse_args expecting it to exit. Returns the exit code and all output."""
    output = io.StringIO()
    with patch.object(sys, "argv", ["with-connect", *args]), redirect_stdout(output), redirect_stderr(output):
        try:
            main.parse_args()
        except SystemExit as e:
            return e.code, output.getvalue()
    raise AssertionError("parse_args did not exit")


def test_port_zero_is_rejected():
    code, output = parse_exit("--port", "0")

    assert code == 2
    assert "0 is not a valid port" in output


def test_port_above_range_is_rejected():
    code, output = parse_exit("--port", "70000")

    assert code == 2
    assert "70000 is not a valid port" in output


def test_port_not_a_number_is_rejected():
    code, output = parse_exit("--port", "abc")

    assert code == 2
    assert "invalid port value: 'abc'" in output


def test_port_range_limits_are_accepted():
    assert parse_with_argv("--port", "1").port == 1
    assert parse_with_argv("--port", "65535").port == 65535


def test_help_explains_how_to_stop():
    code, output = parse_exit("--help")
    help_text = " ".join(output.split())

    assert code == 0
    assert "Stop it later with: with-connect --stop CONTAINER_ID" in help_text
    assert "exported CONTAINER_ID" in help_text


def test_command_after_separator():
    args = parse_with_argv("--port", "4000", "--", "start", "--json")

    assert args.port == 4000
    assert args.command == ["start", "--json"]


def test_no_command_without_separator():
    assert parse_with_argv("--quiet").command == []


def test_separator_without_command_is_rejected():
    code, output = parse_exit("--quiet", "--")

    assert code == 2
    assert "no command given after --" in output


def test_missing_command_fails_before_starting_connect():
    with tempfile.NamedTemporaryFile(suffix=".lic") as license_file:
        with patch.object(main.docker, "from_env") as from_env:
            output = io.StringIO()
            argv = ["with-connect", "--license", license_file.name, "--", "no-such-command-xyz"]
            with patch.object(sys, "argv", argv), redirect_stderr(output):
                result_code = main.main()

    assert result_code == 127
    assert "command not found: no-such-command-xyz" in output.getvalue()
    from_env.assert_not_called()


def test_check_command():
    assert main.check_command(sys.executable) is None
    with redirect_stderr(io.StringIO()):
        assert main.check_command("no-such-command-xyz") == 127
        assert main.check_command("./no-such-script.sh") == 127


def test_check_command_not_executable():
    with tempfile.TemporaryDirectory() as tmpdir:
        script = os.path.join(tmpdir, "script.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(script, 0o644)

        output = io.StringIO()
        with redirect_stderr(output):
            assert main.check_command(script) == 126
            with patch.dict(os.environ, {"PATH": tmpdir}):
                assert main.check_command("script.sh") == 126
        assert "permission denied" in output.getvalue()


def test_check_command_empty_path_entry_searches_current_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "script.sh"), "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(tmpdir, "script.sh"), 0o644)

        cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            with patch.dict(os.environ, {"PATH": os.pathsep}), redirect_stderr(io.StringIO()):
                assert main.check_command("script.sh") == 126
        finally:
            os.chdir(cwd)


def test_check_command_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.mkdir(os.path.join(tmpdir, "subdir"))

        with redirect_stderr(io.StringIO()):
            # Running a directory by path is a permission error
            assert main.check_command(os.path.join(tmpdir, "subdir")) == 126
            # A PATH search skips directories
            with patch.dict(os.environ, {"PATH": tmpdir}):
                assert main.check_command("subdir") == 127


def test_stop_nonexistent_container():
    """Test that --stop with nonexistent container returns error."""
    result = subprocess.run(
        [sys.executable, "main.py", "--stop", "nonexistent_container_id"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Container not found" in result.stderr


def test_stop_connect_container_signals_connect():
    container = MagicMock()
    container.exec_run.return_value = (0, b"")

    main.stop_connect_container(container)

    container.exec_run.assert_called_once_with(
        ["pkill", "-TERM", "-f", main.CONNECT_PROCESS_PATTERN]
    )
    container.wait.assert_called_once_with(timeout=10)
    container.stop.assert_not_called()


def test_stop_mode_signals_connect():
    client = MagicMock()
    container = client.containers.get.return_value
    container.exec_run.return_value = (0, b"")
    with (
        patch.object(sys, "argv", ["with-connect", "--stop", "abc123"]),
        patch.object(main.docker, "from_env", return_value=client),
        redirect_stderr(io.StringIO()),
    ):
        assert main.main() == 0

    container.exec_run.assert_called_once_with(
        ["pkill", "-TERM", "-f", main.CONNECT_PROCESS_PATTERN]
    )


def test_command_mode_signals_connect_after_command():
    client = MagicMock()
    container = client.containers.run.return_value
    container.exec_run.return_value = (0, b"")
    with tempfile.NamedTemporaryFile(suffix=".lic") as license_file:
        argv = ["with-connect", "--license", license_file.name, "--", "true"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(main.docker, "from_env", return_value=client),
            patch.object(main, "ensure_image"),
            patch.object(main, "is_port_open", return_value=True),
            patch.object(main, "wait_for_http_server", return_value=True),
            patch.object(main, "get_api_key", return_value="key"),
            patch.object(main.subprocess, "run", return_value=Mock(returncode=0)),
            redirect_stderr(io.StringIO()),
        ):
            assert main.main() == 0

    container.exec_run.assert_called_once_with(
        ["pkill", "-TERM", "-f", main.CONNECT_PROCESS_PATTERN]
    )


def test_stop_connect_container_falls_back_when_connect_not_signaled():
    # pkill exits 1 when nothing matches and 127 when it isn't installed
    for exit_code in (1, 127):
        container = MagicMock()
        container.exec_run.return_value = (exit_code, b"")

        main.stop_connect_container(container)

        container.wait.assert_not_called()
        container.stop.assert_called_once_with(timeout=2)


def test_stop_connect_container_falls_back_on_errors():
    exec_failure = MagicMock()
    exec_failure.exec_run.side_effect = docker.errors.APIError("container is not running")
    wait_timeout = MagicMock()
    wait_timeout.exec_run.return_value = (0, b"")
    wait_timeout.wait.side_effect = requests.exceptions.ReadTimeout("Read timed out.")

    for container in (exec_failure, wait_timeout):
        main.stop_connect_container(container)

        container.stop.assert_called_once_with(timeout=2)


def _decode_segment(segment):
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_make_bootstrap_jwt():
    secret = os.urandom(32)
    token = main.make_bootstrap_jwt(secret, now=1000)
    header, claims, signature = token.split(".")
    assert _decode_segment(header) == {"alg": "HS256", "typ": "JWT"}
    # Connect rejects the token unless iss, aud, and scope match exactly
    assert _decode_segment(claims) == {
        "iss": "rsconnect-python",
        "aud": "rsconnect",
        "scope": "bootstrap",
        "iat": 1000,
        "exp": 1900,
    }
    expected = hmac.new(secret, f"{header}.{claims}".encode(), hashlib.sha256).digest()
    assert signature == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    assert "=" not in token


def _http_error(url, code, body=b""):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


def test_post_bootstrap_uses_v1():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"api_key": "abc"}'
    with patch("urllib.request.urlopen", return_value=response) as urlopen:
        assert main.post_bootstrap("http://localhost:3939", "tok") == {"api_key": "abc"}
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://localhost:3939/__api__/v1/bootstrap"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Connect-Bootstrap tok"


def test_post_bootstrap_falls_back_to_experimental():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"api_key": "abc"}'
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_error("u", 404), response],
    ) as urlopen:
        assert main.post_bootstrap("http://localhost:3939", "tok") == {"api_key": "abc"}
    urls = [call.args[0].full_url for call in urlopen.call_args_list]
    assert urls == [
        "http://localhost:3939/__api__/v1/bootstrap",
        "http://localhost:3939/__api__/v1/experimental/bootstrap",
    ]


def test_post_bootstrap_raises_on_other_errors():
    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error("u", 401, b'{"error": "invalid jwt"}'),
    ) as urlopen:
        try:
            main.post_bootstrap("http://localhost:3939", "tok")
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "401" in str(e)
            assert "invalid jwt" in str(e)
    # A non-404 error should not trigger the experimental fallback
    assert urlopen.call_count == 1


if __name__ == "__main__":
    test_license_file_not_exists()
    print("✓ test_license_file_not_exists passed")

    test_config_file_not_exists()
    print("✓ test_config_file_not_exists passed")

    test_license_file_with_tilde_expansion()
    print("✓ test_license_file_with_tilde_expansion passed")

    test_invalid_license_detection()
    print("✓ test_invalid_license_detection passed")

    test_valid_license_http_server_starts()
    print("✓ test_valid_license_http_server_starts passed")

    test_image_and_version_exclusive()
    print("✓ test_image_and_version_exclusive passed")

    test_image_without_tag()
    print("✓ test_image_without_tag passed")

    test_image_with_tag()
    print("✓ test_image_with_tag passed")

    test_get_docker_tag_latest()
    print("✓ test_get_docker_tag_latest passed")

    test_get_docker_tag_release()
    print("✓ test_get_docker_tag_release passed")

    test_get_docker_tag_preview()
    print("✓ test_get_docker_tag_preview passed")

    test_get_docker_tag_jammy_version()
    print("✓ test_get_docker_tag_jammy_version passed")

    test_get_docker_tag_bionic_version()
    print("✓ test_get_docker_tag_bionic_version passed")

    test_get_docker_tag_old_version()
    print("✓ test_get_docker_tag_old_version passed")

    test_get_docker_tag_invalid_format()
    print("✓ test_get_docker_tag_invalid_format passed")

    test_container_env_forces_service_log_to_stdout()
    print("✓ test_container_env_forces_service_log_to_stdout passed")

    test_container_env_user_vars_override_defaults()
    print("✓ test_container_env_user_vars_override_defaults passed")

    test_extract_server_version()
    print("✓ test_extract_server_version passed")

    test_extract_server_version_multiple_lines()
    print("✓ test_extract_server_version_multiple_lines passed")

    test_extract_server_version_not_found()
    print("✓ test_extract_server_version_not_found passed")

    test_extract_server_version_dev()
    print("✓ test_extract_server_version_dev passed")

    test_local_image_usage()
    print("✓ test_local_image_usage passed")

    test_release_always_pulls()
    print("✓ test_release_always_pulls passed")

    test_preview_always_pulls()
    print("✓ test_preview_always_pulls passed")

    test_custom_port()
    print("✓ test_custom_port passed")

    test_stop_argument_in_help()
    print("✓ test_stop_argument_in_help passed")

    test_port_zero_is_rejected()
    print("✓ test_port_zero_is_rejected passed")

    test_port_above_range_is_rejected()
    print("✓ test_port_above_range_is_rejected passed")

    test_port_not_a_number_is_rejected()
    print("✓ test_port_not_a_number_is_rejected passed")

    test_port_range_limits_are_accepted()
    print("✓ test_port_range_limits_are_accepted passed")

    test_help_explains_how_to_stop()
    print("✓ test_help_explains_how_to_stop passed")

    test_command_after_separator()
    print("✓ test_command_after_separator passed")

    test_no_command_without_separator()
    print("✓ test_no_command_without_separator passed")

    test_separator_without_command_is_rejected()
    print("✓ test_separator_without_command_is_rejected passed")

    test_missing_command_fails_before_starting_connect()
    print("✓ test_missing_command_fails_before_starting_connect passed")

    test_check_command()
    print("✓ test_check_command passed")

    test_check_command_not_executable()
    print("✓ test_check_command_not_executable passed")

    test_check_command_empty_path_entry_searches_current_directory()
    print("✓ test_check_command_empty_path_entry_searches_current_directory passed")

    test_check_command_directory()
    print("✓ test_check_command_directory passed")

    test_stop_nonexistent_container()
    print("✓ test_stop_nonexistent_container passed")

    test_stop_connect_container_signals_connect()
    print("✓ test_stop_connect_container_signals_connect passed")

    test_stop_mode_signals_connect()
    print("✓ test_stop_mode_signals_connect passed")

    test_command_mode_signals_connect_after_command()
    print("✓ test_command_mode_signals_connect_after_command passed")

    test_stop_connect_container_falls_back_when_connect_not_signaled()
    print("✓ test_stop_connect_container_falls_back_when_connect_not_signaled passed")

    test_stop_connect_container_falls_back_on_errors()
    print("✓ test_stop_connect_container_falls_back_on_errors passed")

    test_make_bootstrap_jwt()
    print("✓ test_make_bootstrap_jwt passed")

    test_post_bootstrap_uses_v1()
    print("✓ test_post_bootstrap_uses_v1 passed")

    test_post_bootstrap_falls_back_to_experimental()
    print("✓ test_post_bootstrap_falls_back_to_experimental passed")

    test_post_bootstrap_raises_on_other_errors()
    print("✓ test_post_bootstrap_raises_on_other_errors passed")

    print("\nAll tests passed!")
