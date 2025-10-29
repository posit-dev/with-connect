import argparse
import base64
import os
import socket
import subprocess
import sys
import time

import docker
from rsconnect.api import RSConnectClient, RSConnectServer
from rsconnect.json_web_token import TokenGenerator


IMAGE = "rstudio/rstudio-connect"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run Posit Connect with optional command execution"
    )
    parser.add_argument(
        "--version",
        default="2025.09.0",
        help="Posit Connect version (default: 2025.09.0)",
    )
    parser.add_argument(
        "--license",
        default="./rstudio-connect.lic",
        help="Path to Posit Connect license file (default: ./rstudio-connect.lic)",
    )
    parser.add_argument(
        "--config", help="Path to rstudio-connect.gcfg configuration file"
    )
    parser.add_argument(
        "-e",
        "--env",
        action="append",
        dest="env_vars",
        help="Environment variables to pass to command (format: KEY=VALUE)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress indicators during image pull",
    )

    # Handle -- separator and capture remaining args
    if "--" in sys.argv:
        separator_index = sys.argv.index("--")
        main_args = sys.argv[1:separator_index]
        command_args = sys.argv[separator_index + 1 :]
    else:
        main_args = sys.argv[1:]
        command_args = []

    args = parser.parse_args(main_args)
    args.command = command_args
    return args


def get_docker_tag(version: str) -> str:
    parts = version.split(".")
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
    # Set platform to linux/amd64 for ARM compatibility (no ARM images available yet)
    if args.quiet:
        print(f"Pulling image {IMAGE}:{tag}...")
    else:
        # Set end="" to avoid newline, flush=True to ensure immediate output
        print(f"Pulling image {IMAGE}:{tag}...", end="", flush=True)

    try:
        # Use low-level API to stream pull progress
        pull_stream = client.api.pull(
            IMAGE, tag=tag, platform="linux/amd64", stream=True, decode=True
        )

        # Print dots periodically to show progress without verbose output
        dot_count = 0
        for chunk in pull_stream:
            if "status" in chunk:
                # Print a dot every few chunks to show activity
                dot_count += 1
                if dot_count % 10 == 0:
                    if not args.quiet:
                        print(".", end="", flush=True)

        if not args.quiet:
            print()  # Newline after dots

        # Get the pulled image to confirm success
        client.images.get(f"{IMAGE}:{tag}")
        print(f"Successfully pulled {IMAGE}:{tag}")
    except Exception as e:
        raise RuntimeError(f"Failed to pull image: {e}")

    mounts = [
        docker.types.services.Mount(
            type="bind",
            read_only=True,
            source=os.path.abspath(os.path.expanduser(args.license)),
            target="/var/lib/rstudio-connect/rstudio-connect.lic",
        ),
    ]

    if args.config:
        mounts.append(
            docker.types.services.Mount(
                type="bind",
                read_only=True,
                source=os.path.abspath(os.path.expanduser(args.config)),
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
        platform="linux/amd64",
        environment={
            # "CONNECT_TENSORFLOW_ENABLED": "false",
            "CONNECT_BOOTSTRAP_ENABLED": "true",
            "CONNECT_BOOTSTRAP_SECRETKEY": bootstrap_secret,
        },
    )

    print("Waiting for port 3939 to open...")
    if not is_port_open("localhost", 3939, timeout=60.0):
        print("\nContainer logs:")
        print(container.logs().decode("utf-8", errors="replace"))
        container.stop()
        raise RuntimeError("Posit Connect did not start within 60 seconds.")

    print("Waiting for HTTP server to start...")
    if not wait_for_http_server(container, timeout=60.0, poll_interval=2.0):
        print("\nContainer logs:")
        print(container.logs().decode("utf-8", errors="replace"))
        container.stop()
        raise RuntimeError(
            "Posit Connect did not log HTTP server start within 60 seconds."
        )

    api_key = get_api_key(bootstrap_secret, container)

    # Execute user command if provided
    exit_code = 0
    if args.command:
        try:
            env = {
                **os.environ,
                "CONNECT_API_KEY": api_key,
                "CONNECT_SERVER": "http://localhost:3939",
            }
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
    Wait until the container logs contain the HTTP server start message.
    Supports both newer format 'Starting HTTP server on [::]:3939'
    and older format 'Starting HTTP server on :3939'

    :param container: docker.models.containers.Container instance
    :param timeout: Maximum seconds to wait (default 60)
    :param poll_interval: Seconds between log checks (default 2)
    :return: True if message found before timeout, False otherwise
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        logs = container.logs().decode("utf-8", errors="replace")

        if "Starting HTTP server on" in logs and ":3939" in logs:
            return True
        time.sleep(poll_interval)
    return False


def get_api_key(bootstrap_secret: str, container) -> str:
    try:
        # Generate bootstrap token from secret
        token_gen = TokenGenerator(bootstrap_secret)
        bootstrap_token = token_gen.bootstrap()
        
        # Create server connection with bootstrap JWT
        server = RSConnectServer("http://localhost:3939", None, bootstrap_jwt=bootstrap_token)
        client = RSConnectClient(server)
        
        # Call bootstrap endpoint
        response = client.bootstrap()
        
        # Extract API key from response
        if response and "api_key" in response:
            api_key = response["api_key"]
            if not api_key:
                print("\nContainer logs:")
                print(container.logs().decode("utf-8", errors="replace"))
                raise RuntimeError("Bootstrap succeeded but returned empty API key")
            return api_key
        else:
            print("\nContainer logs:")
            print(container.logs().decode("utf-8", errors="replace"))
            raise RuntimeError(f"Bootstrap returned unexpected response: {response}")
    except Exception as e:
        print("\nContainer logs:")
        print(container.logs().decode("utf-8", errors="replace"))
        raise RuntimeError(f"Failed to bootstrap Connect and retrieve API key: {e}")


if __name__ == "__main__":
    main()
