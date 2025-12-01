import argparse
import base64
import os
import re
import socket
import subprocess
import sys
import time

import docker
from rsconnect.api import RSConnectClient, RSConnectServer
from rsconnect.json_web_token import TokenGenerator


DEFAULT_IMAGE = "rstudio/rstudio-connect"


def parse_args():
    """
    Parse command line arguments.
    
    Handles the special -- separator to distinguish between tool arguments
    and the command to run against Connect.
    """
    parser = argparse.ArgumentParser(
        description="Run Posit Connect with optional command execution"
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"Container image to use (default: {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--version",
        default="release",
        help="Posit Connect version (default: release, the latest stable release)",
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
        help="Environment variables to pass to Docker container (format: KEY=VALUE)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress indicators during image pull",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3939,
        help="Port to map the Connect container to (default: 3939)",
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


def has_local_image(client, image_name: str) -> bool:
    """
    Check if a Docker image exists in the local cache.
    
    Used to avoid unnecessary pulls when the image is already available locally.
    """
    try:
        client.images.get(image_name)
        return True
    except docker.errors.ImageNotFound:
        return False


def pull_image(client, image_name: str, tag: str, quiet: bool) -> None:
    """
    Pull a Docker image from the registry.
    
    Displays progress indicators (dots) unless quiet mode is enabled.
    Always pulls for linux/amd64 platform for ARM compatibility.
    """
    if quiet:
        print(f"Pulling image {image_name}...")
    else:
        print(f"Pulling image {image_name}...", end="", flush=True)

    pull_stream = client.api.pull(
        IMAGE, tag=tag, platform="linux/amd64", stream=True, decode=True
    )

    dot_count = 0
    for chunk in pull_stream:
        if "status" in chunk:
            dot_count += 1
            if dot_count % 10 == 0 and not quiet:
                print(".", end="", flush=True)

    if not quiet:
        print()

    print(f"Successfully pulled {image_name}")


def ensure_image(client, image_name: str, tag: str, version: str, quiet: bool) -> None:
    """
    Ensure the required Docker image is available.
    
    Strategy:
    - For 'latest'/'release': always pull to get the newest version
    - For specific versions: use local cache if available
    - If pull fails: fall back to local cache if it exists
    - This allows offline usage with cached images
    """
    is_release = version in ("latest", "release")
    
    if not is_release and has_local_image(client, image_name):
        print(f"Using locally cached image {image_name}")
        return
    
    try:
        pull_image(client, image_name, tag, quiet)
    except Exception as e:
        if has_local_image(client, image_name):
            print(f"Pull failed, but using locally cached image {image_name}")
        else:
            raise RuntimeError(f"Failed to pull image and no local copy available: {e}")


def get_docker_tag(version: str) -> str:
    """
    Convert a version string to the appropriate Docker tag.
    
    Maps semantic versions to the correct base image tag based on when
    Connect switched from bionic (Ubuntu 18.04) to jammy (Ubuntu 22.04).
    Also maps 'latest'/'release' to 'jammy' since 'latest' is unmaintained.
    """
    if version in ("latest", "release"):
        # For the rstudio/rstudio-connect image, "jammy" is currently used
        # for the latest stable release. "latest" never gets updated and points
        # to 2022.08.0, which, aside from being misleading, also does not
        # have the bootstrap endpoint that this utility relies on.
        return "jammy"

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


def main() -> int:
    """
    Main entry point for the with-connect CLI tool.
    
    Orchestrates the full workflow:
    1. Parse arguments and validate file paths
    2. Ensure Docker image is available
    3. Start Connect container with license and optional config
    4. Wait for Connect to start and validate license
    5. Bootstrap and retrieve API key
    6. Execute user command with CONNECT_API_KEY and CONNECT_SERVER set
    7. Stop container and exit with command's exit code
    """
    args = parse_args()

    license_path = os.path.abspath(os.path.expanduser(args.license))
    if not os.path.exists(license_path):
        raise RuntimeError(f"License file does not exist: {license_path}")

    if args.config:
        config_path = os.path.abspath(os.path.expanduser(args.config))
        if not os.path.exists(config_path):
            raise RuntimeError(f"Config file does not exist: {config_path}")

    client = docker.from_env()
    tag = get_docker_tag(args.version)
    image_name = f"{args.image}:{tag}"

    bootstrap_secret = base64.b64encode(os.urandom(32)).decode("utf-8")

    ensure_image(client, image_name, tag, args.version, args.quiet)

    mounts = [
        docker.types.services.Mount(
            type="bind",
            read_only=True,
            source=license_path,
            target="/var/lib/rstudio-connect/rstudio-connect.lic",
        ),
    ]

    if args.config:
        mounts.append(
            docker.types.services.Mount(
                type="bind",
                read_only=True,
                source=config_path,
                target="/etc/rstudio-connect/rstudio-connect.gcfg",
            )
        )

    # Build container environment with bootstrap settings and user-provided env vars
    container_env = {
        # "CONNECT_TENSORFLOW_ENABLED": "false",
        "CONNECT_BOOTSTRAP_ENABLED": "true",
        "CONNECT_BOOTSTRAP_SECRETKEY": bootstrap_secret,
    }
    if args.env_vars:
        for env_var in args.env_vars:
            if "=" in env_var:
                key, value = env_var.split("=", 1)
                container_env[key] = value

    container = client.containers.run(
        image=image_name,
        detach=True,
        tty=True,
        stdin_open=True,
        privileged=True,
        ports={"3939/tcp": args.port},
        mounts=mounts,
        platform="linux/amd64",
        environment=container_env,
    )

    server_url = f"http://localhost:{args.port}"

    try:
        print(f"Waiting for port {args.port} to open...")
        if not is_port_open("localhost", args.port, timeout=60.0):
            print("\nContainer logs:")
            print(container.logs().decode("utf-8", errors="replace"))
            raise RuntimeError("Posit Connect did not start within 60 seconds.")

        print("Waiting for HTTP server to start...")
        if not wait_for_http_server(container, timeout=60.0, poll_interval=2.0):
            print("\nContainer logs:")
            print(container.logs().decode("utf-8", errors="replace"))
            raise RuntimeError(
                "Posit Connect did not log HTTP server start within 60 seconds."
            )

        api_key = get_api_key(bootstrap_secret, container, server_url)

        # Execute user command if provided
        exit_code = 0
        if args.command:
            try:
                env = {
                    **os.environ,
                    "CONNECT_API_KEY": api_key,
                    "CONNECT_SERVER": server_url,
                }
                result = subprocess.run(args.command, check=True, env=env)
                exit_code = result.returncode
            except subprocess.CalledProcessError as e:
                exit_code = e.returncode

        return exit_code
    finally:
        container.stop()


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


def extract_server_version(logs: str) -> str | None:
    """
    Extract the Posit Connect version from container logs.
    
    Looks for the startup message like 'Starting Posit Connect v2025.09.0'
    and supports dev versions like 'v2025.11.0-dev+29-gd0db52662c'.
    Returns None if version string not found.
    """
    match = re.search(r"Starting Posit Connect v([\d.]+[\w\-+.]*)", logs)
    if match:
        return match.group(1)
    return None


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
    version = None

    while time.time() < deadline:
        logs = container.logs().decode("utf-8", errors="replace")
        if not version:
            version = extract_server_version(logs)
            if version:
                print(f"Running Posit Connect v{version}")

        if "Unable to obtain a valid license" in logs:
            print("\nContainer logs:")
            print(logs)
            container.stop()
            raise RuntimeError(
                "Unable to obtain a valid license. Your Posit Connect license may be expired or invalid. Please check your license file."
            )

        if "Starting HTTP server on" in logs and ":3939" in logs:
            return True
        time.sleep(poll_interval)
    return False


def get_api_key(bootstrap_secret: str, container, server_url: str) -> str:
    """
    Bootstrap Connect and retrieve an API key.
    
    Uses Connect's bootstrap endpoint with a JWT generated from the bootstrap
    secret to create and retrieve an API key. This key is used to authenticate
    commands run against the Connect instance. Requires Connect 2022.10.0+.
    """
    try:
        # Generate bootstrap token from secret
        # The bootstrap_secret we received is base64-encoded, need to decode it
        secret_bytes = base64.b64decode(bootstrap_secret.encode("utf-8"))
        token_gen = TokenGenerator(secret_bytes)
        bootstrap_token = token_gen.bootstrap()

        # Create server connection with bootstrap JWT
        server = RSConnectServer(server_url, None, bootstrap_jwt=bootstrap_token)
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
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
