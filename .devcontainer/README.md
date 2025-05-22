# Python Dev Container for MCP Gmail Email Reader

This dev container sets up a Python 3.13 environment with Node.js, npm, pipx, uv, and recommended VS Code extensions for developing and running an MCP server that reads Gmail emails.

## Features
- Python 3.13 (from official Microsoft devcontainers)
- Node.js 20.x and latest npm (for MCP dev tools)
- pipx and uv for modern Python dependency management
- SSH agent forwarding for secure GitHub and remote access
- Pre-installed extensions: Python, Pylance, Docker, Copilot, Jupyter, and more

## Usage
1. Open this folder in VS Code.
2. When prompted, reopen in container.
3. The environment will be ready for MCP and Python development.
4. Use `uv sync` to install dependencies.
5. Use `uv run mcp dev main.py` to start the MCP server in development mode.

## Notes
- SSH agent forwarding is enabled for secure GitHub operations.
- The container is configured for easy extension and productionization.
- Update `pyproject.toml` to manage Python dependencies with uv.

You can now develop, test, and run your MCP Gmail Email Reader project efficiently in this container.
