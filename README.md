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

- Python 3.13+
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

### Options

- `--version`: Specify the Connect version (default: release). Use "latest" or "release" for the most recent version, or specify a version like "2024.08.0"
- `--license`: Path to license file (default: ./rstudio-connect.lic)
- `--config`: Path to optional rstudio-connect.gcfg configuration file

Example:

```bash
with-connect --version 2024.08.0 --license /path/to/license.lic -- rsconnect deploy manifest .
```

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

## Minimum Version

Posit Connect 2022.10.0 or later is required. Earlier versions did not have the bootstrap endpoint used in this utility.
