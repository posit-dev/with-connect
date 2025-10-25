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
- [rsconnect-python](https://docs.posit.co/rsconnect-python/) CLI

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

Example workflow for deploying to Posit Connect in CI:

```yaml
name: Deploy to Connect
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      
      - name: Install dependencies
        run: |
          pip install uv
          uv pip install git+https://github.com/tdstein/with-connect.git
          uv pip install rsconnect-python
      
      - name: Create license file
        run: echo "${{ secrets.CONNECT_LICENSE }}" > rstudio-connect.lic
      
      - name: Deploy to Connect
        run: |
          with-connect -- rsconnect deploy manifest .
```

## Minimum Version

Posit Connect 2022.10.0 or later is required (when the bootstrap endpoint was added).
