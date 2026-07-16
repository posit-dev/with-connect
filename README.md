# with-connect

A CLI tool and GitHub Action for running Posit Connect in Docker and executing commands against it.

## Installation

Install as a tool using `uv` (recommended):

```bash
uv tool install git+https://github.com/posit-dev/with-connect.git
```

Or install from a local clone for development:

```bash
git clone https://github.com/posit-dev/with-connect.git
cd with-connect
uv tool install -e .
```

## Requirements

- Python 3.13+, or `uv`
- Docker
- A valid Posit Connect license file

## Usage

### Basic Usage

Run Posit Connect with default settings:

```bash
with-connect
```

This will:
1. Pull the specified Posit Connect Docker image
2. Start a container with your license file mounted
3. Wait for Connect to start
4. Bootstrap and retrieve an API key
5. Stop the container

### Running Commands

Execute a command against the running Connect instance:

```bash
with-connect -- rsconnect deploy manifest .
```

Commands after `--` are executed with `CONNECT_API_KEY` and `CONNECT_SERVER` environment variables set.

**Important:** When using the CLI, if you need to run multiple commands or reference the `CONNECT_API_KEY` and `CONNECT_SERVER` environment variables, you must wrap your command in `bash -c` with single quotes:

```bash
with-connect -- bash -c 'curl -f -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content'
```

Without `bash -c`, the environment variables would be evaluated before `with-connect` defines them.

### Options

| Option        | Default                 | Description                                                                                                          |
|---------------|-------------------------|----------------------------------------------------------------------------------------------------------------------|
| `--version`   | `release`               | Posit Connect version. Use "latest" or "release" for the most recent version, or specify a version like "2024.08.0". |
| `--license`   | `./rstudio-connect.lic` | Path to license file. This file must exist and be a valid Connect license.                                           |
| `--image`     |                         | Container image to use, including tag (e.g., `posit/connect:2025.12.0`). Overrides `--version`.                      |
| `--config`    |                         | Path to optional rstudio-connect.gcfg configuration file                                                             |
| `--port`      | `3939`                  | Port to map the Connect container to. Allows running multiple Connect instances simultaneously.                      |
| `-e`, `--env` |                         | Environment variables to pass to the Docker container (format: KEY=VALUE). Can be specified multiple times.          |
| `--stop`      |                         | Stop a running Connect container by ID, or use `CONTAINER_ID` env var if not specified.                              |
| `--reset`     |                         | Reset a running start-only container to its clean baseline (same container, port, and API key), or use `CONTAINER_ID` env var if not specified. |

Example:

```bash
with-connect --version 2024.08.0 --license /path/to/license.lic -- rsconnect deploy manifest .
```

Passing environment variables to the Docker container:

```bash
with-connect -e MY_VAR=value -e ANOTHER_VAR=123 -- rsconnect deploy manifest .
```

You can use this to override Connect server configuration by passing in `CONNECT_` prefixed variables, following https://docs.posit.co/connect/admin/appendix/configuration/#environment-variables.

If you need env vars that are useful for the command running after `--`, just set them in the environment from which you call `with-connect`: the command will inherit that environment.

### Start-Only Mode

If you omit the command after `--`, Connect will start and remain running. The tool outputs shell variables you can use to interact with Connect:

```bash
with-connect --license ./rstudio-connect.lic
# Outputs:
# CONNECT_API_KEY=...
# CONNECT_SERVER=http://localhost:3939
# CONTAINER_ID=...
```

You can eval the output to set the variables in your shell:

```bash
eval $(with-connect --license ./rstudio-connect.lic)
curl -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content

# Stop Connect when done
with-connect --stop "$CONTAINER_ID"
```

`eval` sets these as ordinary shell variables, so pass `"$CONTAINER_ID"` explicitly. The no-argument forms of `--stop`/`--reset` instead read a `CONTAINER_ID` environment variable, which is how the GitHub Action wires them up.

This is useful when you need to run multiple commands or use other tools against the running Connect instance.

### Resetting Connect

Every start-only container can be reset. `with-connect --reset` returns Connect to its clean, just-bootstrapped state — no deployed content, no extra users — **without stopping the container**. The same `CONNECT_API_KEY`, `CONNECT_SERVER`, and `CONTAINER_ID` stay valid, so a test framework can reset between runs and keep the credentials it already holds:

```bash
eval $(with-connect --license ./rstudio-connect.lic)

# ... run a test that deploys content ...

# Reset to a clean Connect between tests
with-connect --reset "$CONTAINER_ID"
# The same $CONNECT_API_KEY and $CONNECT_SERVER still work; Connect is now clean.

# ... run the next test ...

with-connect --stop "$CONTAINER_ID"
```

Reset is fast (usually a few seconds) because it never restarts the container, re-pulls the image, or re-bootstraps. Under the hood, start-only containers run Connect under a keep-alive process so Connect can be cycled in place; the reset restores a snapshot of the data directory captured right after bootstrap. Reset only applies to start-only containers (command mode containers are ephemeral).

`--reset` supports only Connect's default SQLite data directory (`/var/lib/rstudio-connect`). If you override `Server.DataDir` (via `--config` or `CONNECT_SERVER_DATADIR`) or point Connect at an external database, `--reset` refuses to run and reports an error rather than silently leaving that state in place. (Start-only mode itself works fine with a custom data directory; only reset is unsupported there.)

#### Detecting a crashed Connect

Because a start-only container stays running under the keep-alive process, a crashed Connect does **not** stop the container. To make crashes visible, start-only containers are given a healthcheck that probes Connect's `/__ping__` endpoint. Check it during a test run:

```bash
docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID"
# healthy    -> Connect is serving
# unhealthy  -> Connect stopped responding (e.g. crashed) and did not recover
```

Nothing auto-restarts Connect, so a crash stays `unhealthy` until the next `--reset`. Assert on `healthy` (rather than container liveness) if a test needs to confirm Connect stayed up.

> Note: the health status is refreshed on the container's healthcheck interval, so immediately after a `--reset` it may briefly lag (reset polls `/__ping__` directly and returns as soon as Connect is serving). For an immediate readiness signal use `/__ping__`; for "did Connect stay up over time" use the health status.

## GitHub Actions

This project contains a GitHub Action for use in CI/CD workflows. Use the `@main` tag to reference the action.

You will need to store your Posit Connect license file as a GitHub secret (e.g., `CONNECT_LICENSE_FILE`).

### GitHub Action Inputs

The GitHub Action supports the following inputs:

| Input         | Required | Default   | Description                                                                                   |
|---------------|----------|-----------|-----------------------------------------------------------------------------------------------|
| `license`     | Yes      |           | Posit Connect license file contents (store as a GitHub secret)                                |
| `version`     | No       | `release` | Posit Connect version                                                                         |
| `image`       | No       |           | Container image to use, including tag (e.g., `posit/connect:2025.12.0`). Overrides `version`. |
| `config-file` | No       |           | Path to rstudio-connect.gcfg configuration file                                               |
| `port`        | No       | `3939`    | Port to map the Connect container to                                                          |
| `quiet`       | No       | `false`   | Suppress progress indicators during image pull                                                |
| `env`         | No       |           | Environment variables to pass to Docker container (one per line, format: KEY=VALUE)           |
| `command`     | No       |           | Command to run against Connect (omit for start-only mode)                                     |
| `stop`        | No       |           | Container ID to stop (use instead of starting a new container)                                |
| `reset`       | No       |           | Container ID of a start-only container to reset to its clean baseline (use instead of starting a new container) |

### GitHub Action Outputs

When no `command` is provided (start-only mode), the action sets these outputs:

| Output            | Description                              |
|-------------------|------------------------------------------|
| `CONNECT_API_KEY` | Connect API key for authentication       |
| `CONNECT_SERVER`  | Connect server URL (e.g., `http://localhost:3939`) |
| `CONTAINER_ID`    | Docker container ID (use with `stop` input to stop the container) |

### Deploy a Connect Manifest

```yaml
name: Integration tests with Connect
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - name: Test deployment
        uses: posit-dev/with-connect@main
        with:
          version: 2025.09.0
          license: ${{ secrets.CONNECT_LICENSE_FILE }}
          command: rsconnect deploy manifest .
```

### Multiline Commands in GitHub Actions

Unlike the CLI, the GitHub Action automatically wraps commands in `bash -c`, so you can write multiline commands naturally without explicit wrapping:

```yaml
- name: Run multiple commands
  uses: posit-dev/with-connect@main
  with:
    version: 2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    command: |
      echo "Starting deployment"
      rsconnect deploy manifest .
      curl -f -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content
      echo "Deployment complete"
```

The `$CONNECT_API_KEY` and `$CONNECT_SERVER` environment variables are available within your commands.

**Note:** For single-line commands with special characters (like `$` or quotes), wrap the entire command in single quotes to prevent YAML parsing issues:

```yaml
- name: Single line with special characters
  uses: posit-dev/with-connect@main
  with:
    version: 2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    command: 'curl -f -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content'
```

### Set Environment Variables

```yaml
- name: Test deployment with custom env vars
  uses: posit-dev/with-connect@main
  with:
    version: 2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    env: |
      MY_VAR=value
      ANOTHER_VAR=123
    command: rsconnect deploy manifest .
```

### Specify a Custom Container Image

```yaml
- name: Test deployment with custom image
  uses: posit-dev/with-connect@main
  with:
    image: rstudio/rstudio-connect:jammy-2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    command: rsconnect deploy manifest .
```

### Multi-Step Workflows (Start-Only Mode)

For workflows that need to run multiple steps against Connect, or use other actions with the running instance, use start-only mode:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      # Start Connect without a command - it will keep running
      - name: Start Connect
        id: connect
        uses: posit-dev/with-connect@main
        with:
          version: 2025.09.0
          license: ${{ secrets.CONNECT_LICENSE_FILE }}

      # Use the outputs in subsequent steps
      - name: Deploy content
        run: rsconnect deploy manifest .
        env:
          CONNECT_API_KEY: ${{ steps.connect.outputs.CONNECT_API_KEY }}
          CONNECT_SERVER: ${{ steps.connect.outputs.CONNECT_SERVER }}

      # Use another action with Connect
      - name: Run integration tests
        uses: some-other-action@v1
        with:
          connect-url: ${{ steps.connect.outputs.CONNECT_SERVER }}
          api-key: ${{ steps.connect.outputs.CONNECT_API_KEY }}

      # Stop Connect when done
      - name: Stop Connect
        uses: posit-dev/with-connect@main
        with:
          stop: ${{ steps.connect.outputs.CONTAINER_ID }}
```

## Minimum Version

Posit Connect 2022.10.0 or later is required. Earlier versions did not have the bootstrap endpoint used in this utility.
