# Custom devcontainer Dockerfile for MCP Example
FROM mcr.microsoft.com/devcontainers/python:3.13

# Install additional packages
RUN apt-get update \
    && apt-get install -y curl gnupg openssl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x (with compatible npm) using NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

USER vscode
WORKDIR /home/vscode/workspaces/mcp-example

# Install pipx and use it to install uv globally for vscode user
RUN pipx ensurepath && pipx install uv

# Add /home/vscode/.local/bin to PATH
ENV PATH="/home/vscode/.local/bin:${PATH}"

# Set workdir
WORKDIR /workspaces/mcp-example

# Default command (can be overridden by devcontainer.json)
CMD ["bash"]
