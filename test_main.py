import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    
    print("\nAll tests passed!")
