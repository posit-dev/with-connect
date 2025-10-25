import argparse
import base64
import os
import socket
import subprocess
import sys
import time

import docker



IMAGE = "rstudio/rstudio-connect"


def parse_args():
    parser = argparse.ArgumentParser(description="Run Posit Connect with optional command execution")
    parser.add_argument("--version", default="2025.09.0", help="Posit Connect version (default: 2025.09.0)")
    parser.add_argument("--license", default="./rstudio-connect.lic", help="Path to RStudio Connect license file (default: ./rstudio-connect.lic)")
    parser.add_argument("--config", help="Path to rstudio-connect.gcfg configuration file")
    parser.add_argument("-e", "--env", action="append", dest="env_vars", help="Environment variables to pass to command (format: KEY=VALUE)")

    # Handle -- separator and capture remaining args
    if "--" in sys.argv:
        separator_index = sys.argv.index("--")
        main_args = sys.argv[1:separator_index]
        command_args = sys.argv[separator_index + 1:]
    else:
        main_args = sys.argv[1:]
        command_args = []

    args = parser.parse_args(main_args)
    args.command = command_args
    return args


def get_docker_tag(version: str) -> str:
    parts = version.split('.')
    if len(parts) < 2:
        return version
    
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return version
    
    if year > 2023 or (year == 2023 and month > 6):
        return f"jammy-{version}"
    elif year > 2022 or (year == 2022 and month >= 9):
        return f"bionic-{version}"
    else:
        return version


def main():
    args = parse_args()

    client = docker.from_env()
    tag = get_docker_tag(args.version)

    bootstrap_secret = base64.b64encode(os.urandom(32)).decode("utf-8")

    # Ensure image is pulled from Docker Hub
    print(f"Pulling image {IMAGE}:{tag}...")
    try:
        image = client.images.pull(IMAGE, tag=tag)
        print(f"Successfully pulled {image.short_id}")
    except Exception as e:
        print(f"Failed to pull image: {e}")
        return

    mounts = [
        docker.types.services.Mount(
            type="bind",
            read_only=True,
            source=f"{os.getcwd()}/rstudio-connect.lic",
            target="/var/lib/rstudio-connect/rstudio-connect.lic",
        ),
    ]
    
    if args.config:
        mounts.append(
            docker.types.services.Mount(
                type="bind",
                read_only=True,
                source=os.path.abspath(args.config),
                target="/etc/rstudio-connect/rstudio-connect.gcfg",
            )
        )
    
    container = client.containers.run(
        image=f"{IMAGE}:{tag}",
        detach=True,
        tty=True,
        stdin_open=True,
        privileged=True,
        ports={"3939/tcp": 3939},
        mounts=mounts,
        environment={
            # "CONNECT_TENSORFLOW_ENABLED": "false",
            "CONNECT_BOOTSTRAP_ENABLED": "true",
            "CONNECT_BOOTSTRAP_SECRETKEY": bootstrap_secret,
        },
    )

    print("Waiting for port 3939 to open...")
    if not is_port_open("localhost", 3939, timeout=60.0):
        print("Posit Connect did not start within 60 seconds.")
        container.stop()
        return

    print("Waiting for HTTP server to start...")
    if not wait_for_http_server(container, timeout=60.0, poll_interval=2.0):
        print("Posit Connect did not log HTTP server start within 60 seconds.")
        container.stop()
        return

    api_key = get_api_key(bootstrap_secret)

    # Execute user command if provided
    exit_code = 0
    if args.command:
        try:
            env = {**os.environ, "CONNECT_API_KEY": api_key, "CONNECT_SERVER": "http://localhost:3939"}
            if args.env_vars:
                for env_var in args.env_vars:
                    if "=" in env_var:
                        key, value = env_var.split("=", 1)
                        env[key] = value
            result = subprocess.run(args.command, check=True, env=env)
            exit_code = result.returncode
        except subprocess.CalledProcessError as e:
            exit_code = e.returncode

    container.stop()
    sys.exit(exit_code)

def is_port_open(host: str, port: int, timeout: float = 30.0) -> bool:
    """
    Check if a TCP port on a given host is open.

    :param host: Hostname or IP address to check.
    :param port: Port number to check.
    :param timeout: Timeout in seconds (default 3.0).
    :return: True if the port is open (accepting connections), False otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def wait_for_http_server(
    container, timeout: float = 60.0, poll_interval: float = 2.0
) -> bool:
    """
    Wait until the container logs contain the line:
        'Starting HTTP server on [::]:3939'

    :param container: docker.models.containers.Container instance
    :param timeout: Maximum seconds to wait (default 60)
    :param poll_interval: Seconds between log checks (default 2)
    :return: True if message found before timeout, False otherwise
    """
    target = "Starting HTTP server on [::]:3939"
    deadline = time.time() + timeout

    while time.time() < deadline:
        logs = container.logs().decode("utf-8", errors="ignore")

        if target in logs:
            return True
        time.sleep(poll_interval)
    return False

def get_api_key(bootstrap_secret: str) -> str:
    result = subprocess.run(
        [
            "rsconnect",
            "bootstrap",
            "-i",
            "-s",
            "http://localhost:3939",
            "--raw",
        ],
        check=True,
        text=True,
        env={**os.environ, "CONNECT_BOOTSTRAP_SECRETKEY": bootstrap_secret},
        capture_output=True,
    )

    return result.stdout.strip()


if __name__ == "__main__":
    main()
