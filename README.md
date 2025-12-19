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

## GitHub Actions

This project contains a GitHub Action for use in CI/CD workflows. Use the `@v1` tag to get the latest stable version, or `@main` for the development version.

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
| `command`     | Yes      |           | Command to run against Connect                                                                |

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
        uses: posit-dev/with-connect@v1
        with:
          version: 2025.09.0
          license: ${{ secrets.CONNECT_LICENSE_FILE }}
          command: rsconnect deploy manifest .
```

### Multiline Commands in GitHub Actions

Unlike the CLI, the GitHub Action automatically wraps commands in `bash -c`, so you can write multiline commands naturally without explicit wrapping:

```yaml
- name: Run multiple commands
  uses: posit-dev/with-connect@v1
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
  uses: posit-dev/with-connect@v1
  with:
    version: 2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    command: 'curl -f -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content'
```

### Set Environment Variables

```yaml
- name: Test deployment with custom env vars
  uses: posit-dev/with-connect@v1
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
  uses: posit-dev/with-connect@v1
  with:
    image: rstudio/rstudio-connect:jammy-2025.09.0
    license: ${{ secrets.CONNECT_LICENSE_FILE }}
    command: rsconnect deploy manifest .
```

## Minimum Version

Posit Connect 2022.10.0 or later is required. Earlier versions did not have the bootstrap endpoint used in this utility.
