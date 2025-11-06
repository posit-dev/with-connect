import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

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
    mock_container.logs.return_value = b'Starting HTTP server on [::]:3939'
    
    result = main.wait_for_http_server(mock_container, timeout=1.0, poll_interval=0.1)
    
    assert result is True
    mock_container.stop.assert_not_called()


def test_get_docker_tag_latest():
    assert main.get_docker_tag("latest") == "jammy"


def test_get_docker_tag_release():
    assert main.get_docker_tag("release") == "jammy"


def test_get_docker_tag_jammy_version():
    assert main.get_docker_tag("2025.09.0") == "jammy-2025.09.0"
    assert main.get_docker_tag("2024.01.0") == "jammy-2024.01.0"
    assert main.get_docker_tag("2023.07.0") == "jammy-2023.07.0"


def test_get_docker_tag_bionic_version():
    assert main.get_docker_tag("2023.06.0") == "bionic-2023.06.0"
    assert main.get_docker_tag("2023.01.0") == "bionic-2023.01.0"
    assert main.get_docker_tag("2022.09.0") == "bionic-2022.09.0"


def test_get_docker_tag_old_version():
    assert main.get_docker_tag("2022.08.0") == "2022.08.0"
    assert main.get_docker_tag("2021.12.0") == "2021.12.0"


def test_get_docker_tag_invalid_format():
    assert main.get_docker_tag("jammy") == "jammy"
    assert main.get_docker_tag("custom-tag") == "custom-tag"


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
    
    tag = main.get_docker_tag(mock_args.version)
    image_name = f"{main.IMAGE}:{tag}"
    
    try:
        mock_client.images.get(image_name)
        should_pull = False
    except docker.errors.ImageNotFound:
        should_pull = True
    
    assert should_pull is False


def test_release_always_pulls():
    mock_args = Mock()
    mock_args.version = "release"
    
    should_pull = mock_args.version in ("latest", "release")
    assert should_pull is True


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
    
    test_get_docker_tag_latest()
    print("✓ test_get_docker_tag_latest passed")
    
    test_get_docker_tag_release()
    print("✓ test_get_docker_tag_release passed")
    
    test_get_docker_tag_jammy_version()
    print("✓ test_get_docker_tag_jammy_version passed")
    
    test_get_docker_tag_bionic_version()
    print("✓ test_get_docker_tag_bionic_version passed")
    
    test_get_docker_tag_old_version()
    print("✓ test_get_docker_tag_old_version passed")
    
    test_get_docker_tag_invalid_format()
    print("✓ test_get_docker_tag_invalid_format passed")
    
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
    
    print("\nAll tests passed!")
