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

If you need to run a more complex command, like with multiple commands, or if you need to reference `CONNECT_API_KEY` and `CONNECT_SERVER` in the command, you can use `bash -c` and single quotes:

```bash
with-connect -- bash -c 'curl -f -H "Authorization: Key $CONNECT_API_KEY" $CONNECT_SERVER/__api__/v1/content'
```

### Options

- `--version`: Specify the Connect version (default: release). Use "latest" or "release" for the most recent version, or specify a version like "2024.08.0", or a known docker tag.
- `--license`: Path to license file (default: ./rstudio-connect.lic). This file must exist and be a valid Connect license.
- `--config`: Path to optional rstudio-connect.gcfg configuration file
- `--port`: Port to map the Connect container to (default: 3939). Allows running multiple Connect instances simultaneously.
- `-e`, `--env`: Environment variables to pass to the Docker container (format: KEY=VALUE). Can be specified multiple times.

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

### GitHub Action Options

The GitHub Action supports the following inputs:

- `license` (required): Posit Connect license key (store as a GitHub secret)
- `version` (optional): Posit Connect version (default: release)
- `config-file` (optional): Path to rstudio-connect.gcfg configuration file
- `port` (optional): Port to map the Connect container to (default: 3939)
- `quiet` (optional): Suppress progress indicators during image pull (default: false)
- `env` (optional): Environment variables to pass to Docker container (one per line, format: KEY=VALUE)
- `command` (required): Command to run against Connect

Example with environment variables:

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

## Minimum Version

Posit Connect 2022.10.0 or later is required. Earlier versions did not have the bootstrap endpoint used in this utility.
