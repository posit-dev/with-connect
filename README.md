# with-connect

A CLI tool for running Posit Connect in Docker and executing commands against it.

## Installation

Install using `uv`:

```bash
uv pip install git+https://github.com/tdstein/with-connect.git
```

Or install from a local clone:

```bash
git clone https://github.com/tdstein/with-connect.git
cd with-connect
uv pip install -e .
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

- `--version`: Specify the Connect version (default: 2025.09.0)
- `--license`: Path to license file (default: ./rstudio-connect.lic)
- `--config`: Path to optional rstudio-connect.gcfg configuration file

Example:

```bash
with-connect --version 2024.08.0 --license /path/to/license.lic -- rsconnect deploy manifest .
```

## GitHub Actions

This project contains a GitHub Action for use in CI/CD workflows.

```yaml
name: Deploy to Connect
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - name: Test deployment
        uses: nealrichardson/with-connect@dev
        with:
          version: 2025.09.0
          license: ${{ secrets.CONNECT_LICENSE_FILE }}
          command: rsconnect deploy manifest .
```

## Minimum Version

Posit Connect 2022.10.0 or later is required (when the bootstrap endpoint was added).
