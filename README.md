# MCP Example Project

This project contains two Model Context Protocol (MCP) servers:
1. **mcp_demo** - A demo server with basic tools (add function, resources, prompts)
2. **mcp_server_imap_gmail** - An IMAP server for reading Gmail emails

## Prerequisites

- Python 3.13+
- uv package manager
- Node.js (for VS Code MCP integration)

## Installation

Install dependencies using uv:

```bash
uv sync
```

## Running the MCP Servers

### 1. Demo MCP Server (mcp_demo)

The demo server provides basic functionality including an addition tool and sample resources.

```bash
cd mcp_demo
mcp run server.py
```

This will start the demo server with tools like:
- `add` - Add two numbers
- Various resources and prompts for testing

### 2. IMAP Gmail MCP Server (mcp_server_imap_gmail)

The IMAP server allows you to read Gmail emails via IMAP protocol.

```bash
uv run -m mcp_server_imap_gmail --email <your-email> --imap-key <your-password> --port 8090
```

Replace:
- `<your-email>` with your Gmail address
- `<your-password>` with your Gmail app password (not your regular password)

Example:
```bash
uv run -m mcp_server_imap_gmail --email user@gmail.com --imap-key abcd-efgh-ijkl-mnop --port 8090
```

#### Setting up Gmail App Password

1. Enable 2-factor authentication on your Google account
2. Go to Google Account settings > Security > App passwords
3. Generate a new app password for "Mail"
4. Use this 16-character password (not your regular Gmail password)

## VS Code Integration

The project includes MCP configuration in `.vscode/mcp.json` that connects to both servers:

- **imap-demo-server**: `http://127.0.0.1:8090/mcp/`
- **mcp-demo-server**: `http://127.0.0.1:8000/sse`

Make sure both servers are running before using MCP tools in VS Code.

## Available Tools

### Demo Server Tools
- `add(a, b)` - Add two numbers

### IMAP Server Tools
- `list_emails(start_date, end_date, folder)` - List emails from Gmail

## Development

This project uses:
- **FastMCP** for the demo server
- **mcp** library for the IMAP server
- **IMAPClient** for Gmail integration
- **click** for CLI interface

## Troubleshooting

### SSL Issues
The IMAP server includes SSL fallback handling for development environments. If you encounter SSL certificate issues, the server will attempt to use a less secure SSL context.

### Authentication Errors
- Ensure you're using an app password, not your regular Gmail password
- Verify 2-factor authentication is enabled on your Google account
- Check that IMAP is enabled in Gmail settings

### Port Conflicts
If the default ports are in use, you can specify different ports when starting the servers.