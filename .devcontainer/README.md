# Dev Container

Opens a ready-to-run Python 3.13 environment for the Gmail MCP Server.

## What's included

- Python 3.13 + uv (dependency management)
- Node.js 20 (for MCP Inspector: `npx @modelcontextprotocol/inspector`)
- Port 8001 forwarded automatically with a label
- `uv sync` runs on container creation
- `GMAIL_CREDENTIALS_PATH` and `GMAIL_TOKEN_PERSISTENCY_PATH` pre-set to `credentials/`

## Usage

1. Open the project folder in VS Code
2. When prompted, choose **Reopen in Container** (or run `Dev Containers: Reopen in Container` from the command palette)
3. Wait for the container to build and `uv sync` to finish
4. Start the server: `uv run mcp-server`
5. Port 8001 is forwarded — connect your MCP client to `http://localhost:8001/mcp/`

## Notes

- Gmail token persistence is enabled by default inside the container (`credentials/token.json`). Remove the `GMAIL_TOKEN_PERSISTENCY_PATH` line from `devcontainer.json` to revert to in-memory-only behaviour.
- The SSH mount in `devcontainer.json` is best-effort — it will be skipped if `~/.ssh` does not exist on the host.
