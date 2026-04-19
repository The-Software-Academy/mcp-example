# Custom devcontainer for MCP Example
# Node.js is installed via the devcontainer feature (ghcr.io/devcontainers/features/node:1)
# to avoid the NodeSource curl-pipe security risk and ensure a reproducible install.
FROM mcr.microsoft.com/devcontainers/python:3.13

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

USER vscode
WORKDIR /workspaces/mcp-example

# Install uv via pipx so it's available system-wide for the vscode user
RUN pipx ensurepath && pipx install uv

ENV PATH="/home/vscode/.local/bin:${PATH}"
