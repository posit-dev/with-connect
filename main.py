import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import docker


IMAGE = "ghcr.io/posit-dev/connect"
PREVIEW_IMAGE = "ghcr.io/posit-dev/connect-preview"
LEGACY_IMAGE = "rstudio/rstudio-connect"
VERSION = "release"
DEFAULT_PORT = 3939

# Versions at or after this registry cutover land on ghcr.io/posit-dev.
# Older versions only exist on the legacy Docker Hub images.
REGISTRY_CUTOVER_YEAR = 2026
REGISTRY_CUTOVER_MONTH = 4

# Matches the Connect server's command line, for pkill -f
CONNECT_PROCESS_PATTERN = "^/opt/rstudio-connect/bin/connect( |$)"

# Seconds to wait for each Docker API call. docker-py's default of 60 is
# too short for container create on a busy machine.
DOCKER_TIMEOUT = 120
DOCKER_TIMEOUT_ENV = "DOCKER_CLIENT_TIMEOUT"

DESCRIPTION = """\
Starts Posit Connect in Docker and waits until it is ready.

With no command, prints CONNECT_API_KEY, CONNECT_SERVER and CONTAINER_ID
as KEY=value lines on stdout and leaves the container running.
Stop it later with: with-connect --stop CONTAINER_ID
Progress and errors go to stderr.

With "-- COMMAND", runs COMMAND with CONNECT_API_KEY and CONNECT_SERVER
set, then stops the container and exits with the command's exit code.
If COMMAND can't be found, exits with code 127 (126 if it isn't
executable) without starting Connect."""

EPILOG = """\
examples:
  with-connect --license ./rstudio-connect.lic
  with-connect --license ./rstudio-connect.lic -- bash -c 'curl -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content'
"""


def port_number(value: str) -> int:
    """Parse --port. Docker can only map a fixed host port from 1 to 65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port value: '{value}'")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{port} is not a valid port. Use a free port from 1 to 65535.")
    return port


def timeout_seconds(value: str) -> float:
    """Parse --docker-timeout: a positive number of seconds."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid timeout value: '{value}'")
    if not 0 < seconds < float("inf"):
        raise argparse.ArgumentTypeError(f"{value} is not a valid timeout. Use a positive number of seconds.")
    return seconds


def parse_args():
    """
    Parse command line arguments.

    Handles the special -- separator to distinguish between tool arguments
    and the command to run against Connect.
    """
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        default=VERSION,
        help=f"Posit Connect version (default: {VERSION}, the latest stable release)",
    )
    parser.add_argument(
        "--license",
        default="./rstudio-connect.lic",
        help="Path to Posit Connect license file (default: ./rstudio-connect.lic)",
    )
    parser.add_argument(
        "--image",
        help="Container image to use, including tag (use instead of --version)",
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
        help="Suppress image pull progress and the Connect logs shown while Connect starts",
    )
    parser.add_argument(
        "--port",
        type=port_number,
        help=f"Port to map the Connect container to (default: {DEFAULT_PORT}, or a free port if {DEFAULT_PORT} is in use)",
    )
    parser.add_argument(
        "--docker-timeout",
        type=timeout_seconds,
        metavar="SECONDS",
        help=f"Seconds to wait for each Docker API call, such as creating the container "
        f"(default: ${DOCKER_TIMEOUT_ENV} if set, else {DOCKER_TIMEOUT})",
    )
    parser.add_argument(
        "--stop",
        nargs="?",
        default=None,
        const="",  # sentinel for --stop without argument
        metavar="CONTAINER_ID",
        help="Stop a running Connect container by ID (uses the exported CONTAINER_ID env var if not specified)",
    )

    # Handle -- separator and capture remaining args
    if "--" in sys.argv:
        separator_index = sys.argv.index("--")
        main_args = sys.argv[1:separator_index]
        command_args = sys.argv[separator_index + 1 :]
        if not command_args:
            parser.error("no command given after --")
    else:
        main_args = sys.argv[1:]
        command_args = []

    args = parser.parse_args(main_args)
    args.command = command_args
    if args.docker_timeout is None:
        env_timeout = os.environ.get(DOCKER_TIMEOUT_ENV)
        if env_timeout:
            try:
                args.docker_timeout = timeout_seconds(env_timeout)
            except argparse.ArgumentTypeError as e:
                parser.error(f"{DOCKER_TIMEOUT_ENV}: {e}")
        else:
            args.docker_timeout = DOCKER_TIMEOUT
    return args


def check_command(command: str) -> int | None:
    """
    Check that a command can be run, the way a shell would.

    Returns None if the command can be run. Otherwise prints an error and
    returns the shell's exit code: 126 if the command exists but isn't
    executable, 127 if it can't be found.
    """
    if shutil.which(command):
        return None
    if os.path.dirname(command):
        # An explicit path that exists (even a directory) can't be run
        exists = os.path.exists(command)
    else:
        # A PATH search skips directories, and an empty entry means the
        # current directory
        path_dirs = os.environ.get("PATH", os.defpath).split(os.pathsep)
        exists = any(
            os.path.isfile(os.path.join(d or os.curdir, command)) for d in path_dirs
        )
    if exists:
        print(f"Error: permission denied: {command}", file=sys.stderr)
        return 126
    print(f"Error: command not found: {command}", file=sys.stderr)
    return 127


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


def context_docker_host() -> str | None:
    """
    Find the Docker socket from the Docker CLI's current context.

    docker-py only reads DOCKER_HOST and otherwise assumes
    /var/run/docker.sock, but Docker Desktop, Colima, OrbStack and others
    put their socket elsewhere and point a Docker context at it. Use the
    context the docker command would use (DOCKER_CONTEXT, else
    currentContext in the Docker config) when DOCKER_HOST isn't set.

    Only local sockets (unix:// and npipe://) are returned. tcp:// and
    ssh:// contexts need TLS or SSH settings, so they fall back to
    docker-py's defaults. Returns None to use those defaults.
    """
    if os.environ.get("DOCKER_HOST"):
        return None
    try:
        context = docker.ContextAPI.get_context(os.environ.get("DOCKER_CONTEXT") or None)
    except Exception:
        return None  # An unreadable context shouldn't stop the default socket from working
    host = context.Host if context else None
    if not host or not host.startswith(("unix://", "npipe://")):
        return None
    if host == docker.ContextAPI.DEFAULT_CONTEXT.Host:
        return None
    return host


def docker_client(timeout: float = DOCKER_TIMEOUT):
    """
    Connect to the Docker daemon, with a short error if it isn't reachable.

    :param timeout: Seconds to wait for each Docker API call.
    """
    environment = dict(os.environ)
    host = context_docker_host()
    if host:
        environment["DOCKER_HOST"] = host
    try:
        return docker.from_env(timeout=timeout, environment=environment)
    except docker.errors.DockerException as e:
        where = f" at {host} (from the current Docker context)" if host else ""
        raise RuntimeError(f"Could not connect to Docker{where}. Is it running? {e}")


def _force_amd64(base_image: str) -> bool:
    """
    Whether to force linux/amd64 when pulling and running an image.

    The legacy rstudio/* images on Docker Hub and their ghcr.io/rstudio/*
    mirrors are amd64-only, so without an explicit platform pin the pull
    fails on arm64 hosts with "no matching manifest for linux/arm64/v8".
    Force the pin for any image under those namespaces.

    Everything else — bakery's modern ghcr.io/posit-dev/* and posit/*
    images, plus any custom or upstream multi-arch image — trusts
    Docker's manifest selection to resolve the native architecture.
    """
    return base_image.startswith("rstudio/") or base_image.startswith(
        "ghcr.io/rstudio/"
    )


def pull_image(client, base_image: str, tag: str, quiet: bool) -> None:
    """
    Pull a Docker image from the registry.

    Displays progress indicators (dots) unless quiet mode is enabled.
    Forces linux/amd64 for legacy amd64-only images; lets multi-arch
    images resolve to the native host architecture.
    """
    image_name = f"{base_image}:{tag}"

    if quiet:
        print(f"Pulling image {image_name}...", file=sys.stderr)
    else:
        print(f"Pulling image {image_name}...", end="", flush=True, file=sys.stderr)

    pull_kwargs = {"tag": tag, "stream": True, "decode": True}
    if _force_amd64(base_image):
        pull_kwargs["platform"] = "linux/amd64"
    pull_stream = client.api.pull(base_image, **pull_kwargs)

    dot_count = 0
    for chunk in pull_stream:
        if "status" in chunk:
            dot_count += 1
            if dot_count % 10 == 0 and not quiet:
                print(".", end="", flush=True, file=sys.stderr)

    if not quiet:
        print(file=sys.stderr)

    print(f"Successfully pulled {image_name}", file=sys.stderr)


def ensure_image(client, base_image: str, tag: str, version: str, quiet: bool) -> None:
    """
    Ensure the required Docker image is available.

    Strategy:
    - For 'latest'/'release'/'preview': always pull to get the newest version
    - For specific versions: use local cache if available
    - If pull fails: fall back to local cache if it exists
    - This allows offline usage with cached images
    """
    image_name = f"{base_image}:{tag}"
    is_release = version in ("latest", "release", "preview")

    if not is_release and has_local_image(client, image_name):
        print(f"Using locally cached image {image_name}", file=sys.stderr)
        return

    try:
        pull_image(client, base_image, tag, quiet)
    except Exception as e:
        if has_local_image(client, image_name):
            print(f"Pull failed, but using locally cached image {image_name}", file=sys.stderr)
        else:
            raise RuntimeError(f"Failed to pull image and no local copy available: {e}")


def parse_image_spec(image: str) -> tuple[str, str, bool]:
    """
    Parse an image specification into base image and tag.

    Returns:
        tuple[str, str, bool]: (base_image, tag, used_default_tag)
    """
    try:
        base_image, tag = image.rsplit(":", 1)
        return (base_image, tag, False)
    except ValueError:
        return (image, "latest", True)


def build_container_env(bootstrap_secret: str, env_vars: list[str] | None) -> dict[str, str]:
    """
    Build the container environment: bootstrap and logging settings first,
    then user-provided KEY=VALUE env vars, which override them.

    with-connect detects startup by reading the container's stdout for
    "Starting HTTP server on", an INFO-level service log message. A --config
    file that sends the service log to a file or raises its level above INFO
    would hide that message, so force both here. CONNECT_* env vars take
    precedence over the config file.
    """
    container_env = {
        # "CONNECT_TENSORFLOW_ENABLED": "false",
        "CONNECT_BOOTSTRAP_ENABLED": "true",
        "CONNECT_BOOTSTRAP_SECRETKEY": bootstrap_secret,
        "CONNECT_LOGGING_SERVICELOG": "STDOUT",
        "CONNECT_LOGGING_SERVICELOGLEVEL": "INFO",
    }
    if env_vars:
        for env_var in env_vars:
            if "=" in env_var:
                key, value = env_var.split("=", 1)
                container_env[key] = value
    return container_env


def get_docker_tag(version: str) -> tuple[str, str]:
    """
    Convert a version string to the appropriate Docker image and tag.

    Versions at or after the registry cutover (2026.04) come from
    ghcr.io/posit-dev/connect, where the tag is just the version and the
    image is a multi-arch manifest defaulting to the latest OS variant.

    Older versions continue to resolve against rstudio/rstudio-connect on
    Docker Hub with the legacy OS-prefixed tags (jammy- for the Ubuntu
    22.04 era, bionic- for Ubuntu 18.04).

    'latest' / 'release' map to ghcr.io/posit-dev/connect:latest. 'preview'
    maps to ghcr.io/posit-dev/connect-preview:daily.

    Returns:
        tuple[str, str]: (base_image, tag)
    """
    if version == "preview":
        return (PREVIEW_IMAGE, "daily")

    if version in ("latest", "release"):
        return (IMAGE, "latest")

    parts = version.split(".")
    if len(parts) < 2:
        return (LEGACY_IMAGE, version)

    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return (LEGACY_IMAGE, version)

    if year > REGISTRY_CUTOVER_YEAR or (
        year == REGISTRY_CUTOVER_YEAR and month >= REGISTRY_CUTOVER_MONTH
    ):
        return (IMAGE, version)
    elif year > 2023 or (year == 2023 and month > 6):
        return (LEGACY_IMAGE, f"jammy-{version}")
    elif year > 2022 or (year == 2022 and month >= 9):
        return (LEGACY_IMAGE, f"bionic-{version}")
    else:
        return (LEGACY_IMAGE, version)


def stop_connect_container(container) -> None:
    """
    Stop a Connect container promptly.

    Newer images run startup.sh as PID 1, and a shell running as PID 1
    ignores SIGTERM, so container.stop() waits out its 10 second grace
    period and then kills the container. Signaling the Connect process
    directly lets it shut down cleanly in under a second, and the container
    exits with it. If that doesn't work, fall back to a short stop timeout.
    """
    try:
        exit_code, _ = container.exec_run(
            ["pkill", "-TERM", "-f", CONNECT_PROCESS_PATTERN]
        )
        if exit_code == 0:
            container.wait(timeout=10)
            return
    except Exception:
        pass  # e.g. no pkill in the image, or Connect didn't exit in time
    container.stop(timeout=2)


def run() -> int:
    """
    Run the with-connect workflow and return the exit code.

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

    # Handle --stop mode: just stop the container and exit
    if args.stop is not None:
        container_id = args.stop or os.environ.get("CONTAINER_ID")
        if not container_id:
            raise RuntimeError("No container ID provided and CONTAINER_ID environment variable not set")
        client = docker_client(args.docker_timeout)
        try:
            container = client.containers.get(container_id)
            stop_connect_container(container)
            print(f"Stopped container {container_id}", file=sys.stderr)
            return 0
        except docker.errors.NotFound:
            raise RuntimeError(f"Container not found: {container_id}")
        except Exception as e:
            raise RuntimeError(f"Failed to stop container {container_id}: {e}")

    # Fail before starting Connect if the command can't be run
    if args.command:
        exit_code = check_command(args.command[0])
        if exit_code is not None:
            return exit_code

    license_path = os.path.abspath(os.path.expanduser(args.license))
    if not os.path.exists(license_path):
        raise RuntimeError(f"License file does not exist: {license_path}")

    if args.config:
        config_path = os.path.abspath(os.path.expanduser(args.config))
        if not os.path.exists(config_path):
            raise RuntimeError(f"Config file does not exist: {config_path}")

    if args.image and args.version != VERSION:
        raise RuntimeError("Cannot specify both 'image' and 'version'")

    client = docker_client(args.docker_timeout)

    if args.image:
        base_image, tag, used_default = parse_image_spec(args.image)
        if used_default:
            print(f"No tag specified for image '{args.image}'. Using default tag 'latest'.", file=sys.stderr)
    else:
        base_image, tag = get_docker_tag(args.version)
    image_name = f"{base_image}:{tag}"

    bootstrap_secret = base64.b64encode(os.urandom(32)).decode("utf-8")

    ensure_image(client, base_image, tag, args.version, args.quiet)

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

    container_env = build_container_env(bootstrap_secret, args.env_vars)
    port = args.port
    if port is None and is_port_free(DEFAULT_PORT):
        port = DEFAULT_PORT

    create_kwargs = {
        "image": image_name,
        "tty": True,
        "stdin_open": True,
        "privileged": True,
        # None has Docker assign a free host port
        "ports": {"3939/tcp": port},
        "mounts": mounts,
        "environment": container_env,
    }
    if _force_amd64(base_image):
        create_kwargs["platform"] = "linux/amd64"
    # Create and start separately so a container that fails to start
    # can be removed instead of left behind
    try:
        container = client.containers.create(**create_kwargs)
    except Exception as e:
        raise RuntimeError(f"Failed to create container from {image_name}: {e}")
    try:
        container.start()
    except Exception as e:
        try:
            container.remove(force=True)
        except Exception:
            pass  # Don't hide the start error behind a cleanup error
        raise RuntimeError(f"Failed to start container from {image_name}: {e}")

    stop_container = True
    # Echo Connect's startup logs to stderr (stdout is reserved for KEY=value lines)
    startup_logs = ContainerLogs(container, quiet=args.quiet)

    try:
        if port is None:
            port = published_port(container)
            print(f"Port {DEFAULT_PORT} is in use. Using port {port}.", file=sys.stderr)
        server_url = f"http://localhost:{port}"

        print(f"Waiting for port {port} to open...", file=sys.stderr)
        if not is_port_open("localhost", port, timeout=60.0, container=container):
            startup_logs.dump()
            if container.status in ("exited", "dead"):
                raise RuntimeError(
                    f"Posit Connect container exited before port {port} opened."
                )
            raise RuntimeError("Posit Connect did not start within 60 seconds.")

        print("Waiting for HTTP server to start...", file=sys.stderr)
        if not wait_for_http_server(
            container, timeout=60.0, poll_interval=2.0, startup_logs=startup_logs
        ):
            startup_logs.dump()
            raise RuntimeError(
                "Posit Connect did not log HTTP server start within 60 seconds."
            )

        api_key = get_api_key(bootstrap_secret, container, server_url, startup_logs)

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
            except FileNotFoundError:
                print(f"Error: command not found: {args.command[0]}", file=sys.stderr)
                exit_code = 127
            except PermissionError:
                print(f"Error: permission denied: {args.command[0]}", file=sys.stderr)
                exit_code = 126
        else:
            # Start-only mode: output credentials and keep container running
            print(f"CONNECT_API_KEY={api_key}")
            print(f"CONNECT_SERVER={server_url}")
            print(f"CONTAINER_ID={container.id}")
            stop_container = False

        return exit_code
    finally:
        if stop_container:
            # Warn instead of raising so a stop failure doesn't replace the
            # command's exit code or an error that is already being raised
            try:
                stop_connect_container(container)
            except Exception as e:
                print(
                    f"Warning: failed to stop container {container.id}: {e}",
                    file=sys.stderr,
                )


def is_port_open(
    host: str,
    port: int,
    timeout: float = 30.0,
    poll_interval: float = 0.25,
    container=None,
) -> bool:
    """
    Wait until a TCP port on a given host accepts connections.

    Retries until the timeout because a published Docker port may not be
    bound yet right after the container starts.

    :param host: Hostname or IP address to check.
    :param port: Port number to check.
    :param timeout: Maximum seconds to wait (default 30).
    :param poll_interval: Seconds between connection attempts (default 0.25).
    :param container: Optional docker container; stop waiting if it exits.
    :return: True if the port opened before the timeout, False otherwise.
    """
    deadline = time.monotonic() + timeout
    while True:
        if container is not None:
            container.reload()
            if container.status in ("exited", "dead"):
                return False
        remaining = deadline - time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=max(remaining, 0.1)):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(poll_interval, remaining))


def is_port_free(port: int) -> bool:
    """
    Check if a host port is free by trying to bind to it on all interfaces,
    which is how Docker publishes container ports.

    Docker reports a port taken by another container, but Docker Desktop
    doesn't report one taken by a process on the host.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def published_port(container) -> int:
    """
    Return the host port Docker assigned to the container's port 3939.
    """
    container.reload()
    bindings = container.ports.get("3939/tcp") or []
    if not bindings:
        raise RuntimeError("Docker did not assign a host port to the container")
    return int(bindings[0]["HostPort"])


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


class ContainerLogs:
    """
    Read a container's logs and echo new lines to stderr as they appear.

    Each poll() fetches the full log and prints only the complete lines that
    haven't been shown yet, so callers can poll in a loop. dump() prints
    whatever is left when something fails, so no line is printed twice.
    With quiet=True nothing is echoed, and dump() prints the whole log.
    """

    def __init__(self, container, quiet: bool = False):
        self.container = container
        self.quiet = quiet
        self.shown = 0  # bytes of the log already printed

    def poll(self) -> str:
        """Fetch the full log, echo new complete lines, and return the log."""
        logs = self.container.logs()
        if not self.quiet:
            # Hold back a trailing partial line until it is finished
            end = logs.rfind(b"\n", self.shown) + 1
            if end > self.shown:
                self._print(logs[self.shown : end])
                self.shown = end
        return logs.decode("utf-8", errors="replace")

    def dump(self) -> None:
        """Print the part of the log that hasn't been shown yet."""
        logs = self.container.logs()
        if self.shown == 0:
            print("\nContainer logs:", file=sys.stderr)
        elif len(logs) > self.shown:
            print("\nContainer logs (continued):", file=sys.stderr)
        self._print(logs[self.shown :])
        self.shown = len(logs)

    @staticmethod
    def _print(chunk: bytes) -> None:
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        print(text, end="" if text.endswith("\n") else "\n", file=sys.stderr, flush=True)


def wait_for_http_server(
    container,
    timeout: float = 60.0,
    poll_interval: float = 2.0,
    startup_logs: ContainerLogs | None = None,
) -> bool:
    """
    Wait until the container logs contain the HTTP server start message.
    Supports both newer format 'Starting HTTP server on [::]:3939'
    and older format 'Starting HTTP server on :3939'

    :param container: docker.models.containers.Container instance
    :param timeout: Maximum seconds to wait (default 60)
    :param poll_interval: Seconds between log checks (default 2)
    :param startup_logs: ContainerLogs that echoes new log lines to stderr
        while waiting (default: a quiet one that echoes nothing)
    :return: True if message found before timeout, False otherwise
    """
    if startup_logs is None:
        startup_logs = ContainerLogs(container, quiet=True)
    deadline = time.time() + timeout
    version = None

    while time.time() < deadline:
        logs = startup_logs.poll()
        if not version:
            version = extract_server_version(logs)
            if version:
                print(f"Running Posit Connect v{version}", file=sys.stderr)

        if "Unable to obtain a valid license" in logs:
            startup_logs.dump()
            raise RuntimeError(
                "Unable to obtain a valid license. Your Posit Connect license may be expired or invalid. Please check your license file."
            )

        if "Starting HTTP server on" in logs and ":3939" in logs:
            return True
        time.sleep(poll_interval)
    return False


def make_bootstrap_jwt(secret: bytes, now: float | None = None) -> str:
    """
    Create an HS256-signed JWT for Connect's bootstrap endpoint.

    Connect requires exactly these issuer, audience, and scope values (they
    are hard-coded to match what rsconnect-python sends), plus iat and exp.
    """
    if now is None:
        now = time.time()
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": "rsconnect-python",
        "aud": "rsconnect",
        "scope": "bootstrap",
        "iat": int(now),
        "exp": int(now) + 15 * 60,
    }

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def encode_json(obj: dict) -> str:
        return b64url(json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{encode_json(header)}.{encode_json(claims)}"
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def post_bootstrap(server_url: str, token: str) -> dict:
    """
    POST to Connect's bootstrap endpoint and return the decoded JSON response.

    Connect 2024.05.0 added v1/bootstrap; earlier versions only have
    v1/experimental/bootstrap, so fall back to that on a 404.
    """
    for path in ("v1/bootstrap", "v1/experimental/bootstrap"):
        request = urllib.request.Request(
            f"{server_url.rstrip('/')}/__api__/{path}",
            method="POST",
            headers={
                "Authorization": f"Connect-Bootstrap {token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            if e.code == 404 and path == "v1/bootstrap":
                continue
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} from {path}: {body}")
    raise AssertionError("unreachable")


def get_api_key(
    bootstrap_secret: str,
    container,
    server_url: str,
    startup_logs: ContainerLogs | None = None,
) -> str:
    """
    Bootstrap Connect and retrieve an API key.

    Uses Connect's bootstrap endpoint with a JWT generated from the bootstrap
    secret to create and retrieve an API key. This key is used to authenticate
    commands run against the Connect instance. Requires Connect 2022.10.0+.
    On failure, prints the container logs not already shown by startup_logs.
    """
    if startup_logs is None:
        startup_logs = ContainerLogs(container, quiet=True)
    try:
        # The bootstrap_secret we received is base64-encoded, need to decode it
        secret_bytes = base64.b64decode(bootstrap_secret.encode("utf-8"))
        response = post_bootstrap(server_url, make_bootstrap_jwt(secret_bytes))

        api_key = response.get("api_key") if isinstance(response, dict) else None
        if not api_key:
            raise RuntimeError(f"Bootstrap returned unexpected response: {response}")
        return api_key
    except Exception as e:
        startup_logs.dump()
        raise RuntimeError(f"Failed to bootstrap Connect and retrieve API key: {e}")


def main() -> None:
    """
    Main entry point for the with-connect CLI tool.

    Runs the workflow and reports errors as a short message.
    """
    try:
        sys.exit(run())
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
