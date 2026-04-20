# Gmail MCP Server

A didactic [Model Context Protocol](https://modelcontextprotocol.io) server built with FastMCP. One file per concept, every MCP primitive demonstrated with a real Gmail integration. Intended as a learning reference — not a production library.

## What's inside

| MCP Feature | Where |
|---|---|
| **Tools — basic** | `get_current_time`, `calculate` |
| **Tools — Pydantic return** | `analyze_text`, `get_email`, `list_emails` |
| **Tools — side effects** | `create_note`, `send_email`, `reply_to_email` |
| **Tools — progress reporting** | `long_task`, `stream_inbox`, `mark_as_read` |
| **Tools — error handling** | `calculate` (ToolError), `_require_gmail` graceful degradation |
| **Tools — OAuth2 flow** | `gmail_authenticate` — `ctx.elicit_url()` + `notifications/elicitation/complete` |
| **Tool annotations** | `readOnlyHint`, `destructiveHint` on all tools |
| **Resources (static)** | `notes://` — index of all notes |
| **Resources (template)** | `notes://{title}` — read a note by title |
| **Prompts** | 5 prompts: inbox triage, reply drafting, weekly digest, brainstorm, MCP pattern explainer |
| **Lifespan** | Shared Gmail service + notes store, initialised once per process |
| **Context injection** | `ctx: Context` in every async tool |
| **Client logging** | `ctx.info()`, `ctx.debug()`, `ctx.warning()` — structured log stream |
| **MCP sampling** | `ctx.sample()` in `summarize_email` — LLM inference delegated to the host |
| **MCP elicitation (form)** | `ctx.elicit()` in `send_email` / `reply_to_email` — confirmation before sending |
| **MCP elicitation (URL)** | `ctx.elicit_url()` in `gmail_authenticate` — OAuth consent via out-of-band URL |
| **MCP spec OAuth 2.0** | `ConsentOAuthProvider` — discovery, dynamic registration, browser consent page, PKCE |
| **Custom routes** | `GET /health`, `GET /auth-status`, `GET /auth`, `GET /oauth/callback`, `GET+POST /oauth/consent` |
| **StreamableHTTP — stateful** | `stateless_http=False` — persistent GET SSE channel for server-initiated notifications |
| **Graceful degradation** | Gmail tools return a helpful error when credentials are not configured |
| **Antipattern showcase** | `watch_inbox` — documents why MCP has no persistent background push channel |

---

## Quick start (demo tools only — no Gmail credentials needed)

```bash
uv sync
uv run mcp-server

# verify
curl http://localhost:8001/health
# → {"status":"ok","version":"0.2.0"}
```

The server listens on `http://0.0.0.0:8001/mcp`. All demo tools work immediately.

---

## Configuration

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|---|---|---|
| `MCP_PORT` | `8001` | HTTP port |
| `MCP_AUTH_TOKEN` | *(unset)* | Consent-page password. Set to a secret value to enable MCP spec OAuth 2.0 auth; unset = no auth (local dev). You type this password on the browser consent page to approve an MCP client. |
| `GMAIL_CREDENTIALS_PATH` | *(unset)* | Path to `credentials.json` from Google Cloud Console |
| `GMAIL_TOKEN_PERSISTENCY_PATH` | *(unset = in-memory only)* | Set to a file path to persist the OAuth token to disk across server restarts |

**Token security note:** by default the OAuth token is kept in memory and lost on server restart. This is the safe default — set `GMAIL_TOKEN_PERSISTENCY_PATH` only if you explicitly want persistence (e.g. in a dev container or trusted server environment). The devcontainer sets it automatically to `credentials/token.json`.

---

## Gmail setup

Gmail tools require OAuth2 credentials. See **[credentials/README.md](credentials/README.md)** for full step-by-step instructions.

Short version:
1. Create a Google Cloud project and enable the Gmail API
2. Create an OAuth 2.0 "Desktop app" client and download `credentials.json`
3. Save it to `credentials/credentials.json`
4. Set `GMAIL_CREDENTIALS_PATH=./credentials/credentials.json` in `.env`
5. Start the server, then call the `gmail_authenticate` tool from any MCP client — it returns the Google consent URL, click it, grant access, and Gmail tools activate automatically

---

## Running the server

```bash
# Default (no auth, demo tools only)
uv run mcp-server

# With Gmail (token in memory only)
GMAIL_CREDENTIALS_PATH=./credentials/credentials.json uv run mcp-server

# With Gmail + token persisted to disk across restarts
GMAIL_CREDENTIALS_PATH=./credentials/credentials.json \
GMAIL_TOKEN_PERSISTENCY_PATH=./credentials/token.json \
uv run mcp-server

# With MCP OAuth auth (demo: consent page requires typing this password)
MCP_AUTH_TOKEN=mysecret uv run mcp-server

# Full stack: Gmail + persistence + auth
GMAIL_CREDENTIALS_PATH=./credentials/credentials.json \
GMAIL_TOKEN_PERSISTENCY_PATH=./credentials/token.json \
MCP_AUTH_TOKEN=mysecret \
uv run mcp-server

# Custom port
uv run mcp-server --port 9000

# Verbose logging
uv run mcp-server -v    # INFO
uv run mcp-server -vv   # DEBUG
```

---

## Connecting to the server

When `MCP_AUTH_TOKEN` is set the server runs full MCP spec OAuth 2.0. All clients below discover the auth endpoints automatically via `GET /.well-known/oauth-authorization-server` — no manual bearer-token configuration needed. On first connect the client registers itself, redirects your browser to the consent page, and stores the tokens for future sessions.

### Claude Code

The project ships a `.mcp.json` that configures Claude Code automatically:

```json
{
  "mcpServers": {
    "gmail-mcp": {
      "type": "http",
      "url": "http://localhost:8001/mcp/"
    }
  }
}
```

### Google Antigravity

In the Agent pane click **⋯ → MCP Servers → Manage MCP Servers → Edit configuration** and add to `mcp_config.json`:

```json
{
  "mcpServers": {
    "gmail-mcp": {
      "serverUrl": "http://localhost:8001/mcp"
    }
  }
}
```

> **Note:** Antigravity uses `"serverUrl"` — Claude Code and VS Code use `"url"`. The value is otherwise identical.

#### Local setup with Antigravity (no devcontainer)

Antigravity runs on your host machine and does not have access to a devcontainer's forwarded ports. Run the server directly on your machine:

```bash
# 1. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# or: pip install uv

# 2. Install dependencies
uv sync

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env — set GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PERSISTENCY_PATH, etc.

# 4. Start the server
uv run mcp-server
```

Then add the server to Antigravity as shown above. Because you're running locally, `localhost:8001` resolves directly — no port forwarding needed.

**Gmail authentication** works the same way: call `gmail_authenticate` from the Antigravity agent pane, open the URL it returns in your browser, and grant access. The server hot-reloads Gmail without a restart.

**Notes specific to Antigravity:**
- Antigravity does not yet support MCP elicitation — `send_email` and `reply_to_email` will raise a `ToolError` by default because the confirmation prompt cannot be shown. Set `MCP_ELICITATION_FAIL_OPEN=1` in `.env` to allow them to proceed without confirmation (useful for local development, not recommended for production). See the [elicitation section](#mcp-elicitation--form-mode-user-confirmation-mid-tool) for details.
- Antigravity uses `"serverUrl"` (not `"url"`) in its config — the two are otherwise identical.

### VS Code

Configure `.vscode/mcp.json` (already in the repo):

```json
{
  "servers": {
    "gmail-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8001/mcp/"
    }
  }
}
```

### MCP Inspector

```bash
# 1. Install Node.js (includes npx)
#    macOS:  brew install node
#    Ubuntu: sudo apt install nodejs npm
#    Other:  https://nodejs.org

# 2. Run MCP Inspector
npx @modelcontextprotocol/inspector
# Connect to: http://localhost:8001/mcp
```

Kill and restart the Inspector process (not just the browser tab) when reconnecting after a server restart — the Inspector proxy caches the session ID.

---

## Tool reference

### Demo tools (always available)

| Tool | Description | Annotations |
|---|---|---|
| `get_current_time(timezone)` | Current time in any IANA timezone. Returns a typed `TimeResult`. | `readOnlyHint` |
| `create_note(title, content)` | Create or overwrite an in-memory note (persists until server restart). | — |
| `get_note(title)` | Retrieve a note by title. Raises `ToolError` with available titles on miss. | `readOnlyHint` |
| `list_notes()` | List all note titles. | `readOnlyHint` |
| `delete_note(title)` | Delete a note. | `destructiveHint` |
| `analyze_text(text)` | Word count, char count, sentence count, avg word length, language hint. | `readOnlyHint` |
| `calculate(expression)` | Safe arithmetic: `+`, `-`, `*`, `/`, `**`, `%`, `//`. No `eval` injection. | `readOnlyHint` |
| `long_task(steps)` | Progress notifications + structured logging demo. 1–20 steps. | — |

### Gmail tools (require `GMAIL_CREDENTIALS_PATH`)

Call `gmail_authenticate` first if you haven't done so. All Gmail tools return a helpful error if credentials are not configured or the session has expired.

| Tool | Description | Annotations |
|---|---|---|
| `gmail_authenticate()` | Start the OAuth2 flow via `ctx.elicit_url()`. Opens the Google consent page; hot-reloads Gmail when the callback fires. No server restart needed. | — |
| `gmail_logout()` | Clear in-memory credentials and delete the token file if `GMAIL_TOKEN_PERSISTENCY_PATH` is set. | `destructiveHint` |
| `list_emails(query, max_results)` | Search with Gmail query syntax (`"is:unread after:2025/01/01"`). Returns `EmailList`. | `readOnlyHint` |
| `get_email(message_id)` | Full message with headers and plain-text body. Returns `EmailMessage`. | `readOnlyHint` |
| `get_thread(thread_id)` | All messages in a thread, oldest first. | `readOnlyHint` |
| `list_labels()` | All Gmail labels (system + custom). | `readOnlyHint` |
| `mark_as_read(message_ids)` | Batch remove `UNREAD` label. Reports per-message progress. | — |
| `send_email(to, subject, body, cc?)` | Compose and send. Asks for confirmation via elicitation before sending. | — |
| `reply_to_email(message_id, body)` | Reply preserving `In-Reply-To`/`References` threading headers. | — |
| `summarize_email(message_id)` | Fetches body → delegates summarisation to the host LLM via MCP sampling. | `readOnlyHint` |
| `stream_inbox(max_emails)` | Fetch unread inbox emails one-by-one with `notifications/progress` per email. Progress reporting showcase. | `readOnlyHint` |
| `watch_inbox(poll_interval_seconds)` | **Antipattern showcase** — see note below. | `readOnlyHint` |
| `stop_watch_inbox()` | Signal a running `watch_inbox` to stop. | — |

> **`watch_inbox` antipattern:** MCP `notifications/message` and `notifications/progress` are bound to an in-flight tool request — they flow through the POST response body or the GET SSE channel *of that specific request*. There is no spec-native way for a background task to push arbitrary events to a connected client outside a request context. `watch_inbox` works around this by polling Gmail in a background loop and logging results via `ctx.info()`, but the notifications only arrive while the tool call is still open — which means the MCP client's timeout (e.g. MCP Inspector's 60 s default) will kill the tool before it does much useful work. The right architecture for real-time inbox monitoring is a webhook → queue → external push channel, not an MCP long-poll tool.

### Resources

| URI | Description |
|---|---|
| `notes://` | Index of all note titles (one per line). |
| `notes://{title}` | Content of a specific note. |

### Prompts

| Prompt | Arguments | Description |
|---|---|---|
| `triage_inbox` | `date_range` | Prioritise inbox emails with urgency labels and suggested replies. |
| `draft_reply` | `message_id`, `tone` | Draft a reply after reading the full thread. |
| `weekly_digest` | `week_of` | Weekly email report grouped by sender domain. |
| `brainstorm_with_notes` | `topic` | Brainstorm ideas and capture them as notes. |
| `explain_mcp_pattern` | `pattern` | Live demo of an MCP pattern using this server as the example. |

---

## MCP pattern guide

### Lifespan + shared state

FastMCP 3.x lifespan must yield a `dict`. Access it via `ctx.lifespan_context`.

```python
@asynccontextmanager
async def lifespan(server: FastMCP):
    yield {"gmail_service": build_gmail_service(), "notes": {}}

mcp = FastMCP(name="gmail-mcp", lifespan=lifespan)

@mcp.tool()
async def create_note(title: str, content: str, ctx: Context) -> str:
    ctx.lifespan_context["notes"][title] = content
    return f"Note '{title}' saved."
```

### Progress reporting + client logging

```python
@mcp.tool()
async def long_task(steps: int, ctx: Context) -> str:
    await ctx.info(f"Starting {steps} steps")
    for i in range(steps):
        await ctx.report_progress(progress=i, total=steps)
        await ctx.debug(f"Step {i+1}/{steps}")
        await asyncio.sleep(0.25)
    return "Done."
```

Progress notifications require the client to pass a `progressToken` in `_meta`:
```json
{"method":"tools/call","params":{"name":"long_task","arguments":{"steps":5},"_meta":{"progressToken":"my-task"}}}
```

`ctx.info()` / `ctx.debug()` / `ctx.warning()` send `notifications/message` — visible in MCP Inspector's **Notifications** tab.

### MCP sampling (server-side LLM call)

```python
@mcp.tool()
async def summarize_email(message_id: str, ctx: Context) -> str:
    body = fetch_email_body(message_id)
    result = await ctx.sample(f"Summarise in ≤150 words:\n\n{body}")
    return result.text
```

No external API key needed — the host agent (Claude) performs the inference.

### MCP elicitation — form mode (user confirmation mid-tool)

```python
from pydantic import BaseModel

class ConfirmSend(BaseModel):
    confirm: bool = Field(default=False, description="Send the email?")

result = await ctx.elicit("Send email to alice@example.com?", schema=ConfirmSend)
if result.action == "accept" and result.data.confirm:
    # user confirmed
    ...
```

The client renders a native form from the Pydantic schema. Only primitive fields (`str`, `int`, `float`, `bool`) and `list[str]` are allowed — no nested models.

#### Detecting elicitation support

Elicitation support is advertised by the client during the `initialize` handshake. There are two ways to check it:

**Reactive** — call `ctx.elicit()` and catch the `McpError` if the client returns `method not found`:
```python
from mcp.shared.exceptions import McpError

try:
    result = await ctx.elicit(message, schema=MySchema)
except McpError:
    # client does not support elicitation — decide: fail-closed or fail-open
    raise ToolError("This action requires a client that supports elicitation.")
```

**Proactive** — inspect the client's declared capabilities before the round-trip:
```python
caps = ctx.request_context.session._client_params.capabilities
if caps.elicitation is None:
    raise ToolError("This action requires a client that supports elicitation.")
result = await ctx.elicit(message, schema=MySchema)
```

For destructive operations (sending email, deleting data) always **fail-closed** — raise a `ToolError` rather than proceeding silently. Failing open (proceeding without confirmation when elicitation is unavailable) is a security anti-pattern: the user never approved the action.

This server controls the failure mode via `MCP_ELICITATION_FAIL_OPEN`:
- **Unset (default):** fail-closed — `ToolError` if the client does not support elicitation
- **Set to any value:** fail-open — logs a warning and proceeds (useful for development with clients like Antigravity that don't yet support elicitation)

### MCP elicitation — URL mode (OAuth consent)

URL-mode elicitation sends the user to an external URL for interactions that must happen outside the LLM context (OAuth, credentials, payments).

```python
@mcp.tool()
async def gmail_authenticate(ctx: Context) -> str:
    auth_url = "http://localhost:8001/auth"
    elicitation_id = str(uuid.uuid4())

    # Store session so the OAuth callback can send the completion notification
    _pending_oauth["session"] = ctx.request_context.session
    _pending_oauth["elicitation_id"] = elicitation_id

    try:
        result = await ctx.elicit_url(
            message="Click Accept to open the Google OAuth consent page.",
            url=auth_url,
            elicitation_id=elicitation_id,
        )
        if result.action == "accept":
            return "OAuth started — complete the flow in your browser."
        return "Authentication cancelled."
    except Exception:
        # Fallback for clients that don't support URL-mode elicitation yet
        return f"Open this URL in your browser:\n\n{auth_url}"
```

When OAuth completes, the `/oauth/callback` route sends `notifications/elicitation/complete` back through the GET SSE channel — the spec-native signal that the out-of-band interaction is done:

```python
@mcp.custom_route("/oauth/callback", methods=["GET"])
async def oauth_callback(request: Request) -> Response:
    # ... exchange code for token, hot-reload gmail_service ...

    session = _pending_oauth.get("session")
    elicitation_id = _pending_oauth.get("elicitation_id")
    if session and elicitation_id:
        await session.send_elicit_complete(elicitation_id)

    return Response(SUCCESS_HTML, media_type="text/html")
```

`notifications/elicitation/complete` requires `stateless_http=False` (stateful mode) so the persistent GET SSE channel is available. It was added in MCP spec 2025-11-25; clients that don't support it yet will simply ignore the notification.

### Stateful vs stateless HTTP transport

```python
# stateless_http=False  (this server's default)
# - Each POST request gets its own SSE response stream
# - A separate persistent GET /mcp SSE channel carries server-initiated notifications:
#   tools/list_changed, resources/list_changed, notifications/elicitation/complete
# - Required for: background notifications, elicitation/complete
#
# stateless_http=True
# - No persistent SSE channel — every interaction is a single POST/response
# - Simpler, horizontally scalable, but can't send unsolicited notifications
app = mcp.http_app(path="/mcp", stateless_http=False)
```

### MCP spec OAuth 2.0 auth

Set `MCP_AUTH_TOKEN` to a secret value at server startup to enable native MCP OAuth. The value is the **consent-page password** — when a new MCP client tries to connect, you see a browser prompt asking you to type this value before approving the connection. Clients that have already been approved store a refresh token and reconnect silently.

FastMCP wires the full spec automatically when you pass an `AuthProvider` to `FastMCP(auth=...)`:

```python
# server.py — subclass the built-in InMemoryOAuthProvider and add a consent page
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, construct_redirect_uri

class ConsentOAuthProvider(InMemoryOAuthProvider):
    def __init__(self, base_url: str) -> None:
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["mcp:full"],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=["mcp:full"],
        )
        self.pending: dict[str, tuple] = {}

    async def authorize(self, client, params: AuthorizationParams) -> str:
        key = secrets.token_urlsafe(16)
        self.pending[key] = (client, params)
        return f"{self._base_url}/oauth/consent?key={key}"   # → browser consent page

    def approve(self, key: str) -> str | None:
        client, params = self.pending.pop(key)
        code = secrets.token_hex(16)
        self.auth_codes[code] = AuthorizationCode(code=code, ...)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
```

```python
# server.py — provider and FastMCP wired together
if _MCP_AUTH_ENABLED := bool(os.getenv("MCP_AUTH_TOKEN")):
    _oauth_provider = PersistentOAuthProvider(base_url="http://localhost:8001", state_path=Path("credentials/oauth_state.json"))
else:
    _oauth_provider = None

mcp = FastMCP(name="gmail-mcp", lifespan=lifespan, auth=_oauth_provider)
```

FastMCP then adds automatically:
- `GET /.well-known/oauth-authorization-server` — RFC 8414 discovery metadata
- `POST /register` — RFC 7591 dynamic client registration
- `GET /authorize` — redirects browser to `/oauth/consent`
- `POST /token` — code ↔ access + refresh tokens (PKCE enforced)
- `POST /revoke` — token revocation
- `GET /.well-known/oauth-protected-resource/mcp` — RFC 9728 resource metadata
- `RequireAuthMiddleware` on the `/mcp` endpoint

The browser consent page (`/oauth/consent`) is added via `@mcp.custom_route` which exempts it from auth, so the user can approve/deny without already having a token:

```python
@mcp.custom_route("/oauth/consent", methods=["GET"])
async def consent_page(request: Request) -> Response:
    key = request.query_params["key"]
    # render HTML with Approve / Deny buttons

@mcp.custom_route("/oauth/consent", methods=["POST"])
async def consent_action(request: Request) -> Response:
    form = await request.form()
    redirect_url = _oauth_provider.approve(form["key"])  # or .deny()
    return RedirectResponse(redirect_url, status_code=302)
```

**MCP clients handle the entire flow automatically** — no manual bearer token configuration. On first connection they discover the auth server, register themselves, redirect the user to the consent page, and exchange the code for tokens. On subsequent connections they use the refresh token silently.

### Custom routes

```python
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
```

Routes registered with `@mcp.custom_route` are included automatically when `mcp.http_app()` is called. Useful for health checks, auth redirects, and OAuth callbacks on the same port.

---

## Dev container

Open the project in VS Code and choose **Reopen in Container**. The dev container:
- Installs Python 3.13 + uv
- Installs Node.js 20 via a devcontainer feature (no curl-pipe)
- Forwards port 8001 with a label
- Runs `uv sync` automatically
- Sets `GMAIL_CREDENTIALS_PATH` and `GMAIL_TOKEN_PERSISTENCY_PATH` to the `credentials/` directory

Note: the devcontainer explicitly opts in to disk token persistence via `GMAIL_TOKEN_PERSISTENCY_PATH`. Remove that `containerEnv` entry if you prefer in-memory-only behaviour inside the container.

---

## Testing

```bash
# Import check
uv run python -c "from mcp_gmail.server import build_app; print(type(build_app()))"

# Health endpoint (works with or without auth)
curl http://localhost:8001/health

# Auth status
curl http://localhost:8001/auth-status
# → {"gmail_configured": false, "mcp_auth_enabled": false}
```

The raw MCP JSON-RPC examples below assume `MCP_AUTH_TOKEN` is **not set** (no auth). If auth is enabled, complete the OAuth flow through an MCP client first to obtain a bearer token, then pass it as a header:

```bash
# No auth
curl -s -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | grep -o '"name":"[^"]*"'

# With auth (replace <token> with the bearer token issued after OAuth consent)
curl -s -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer <token>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | grep -o '"name":"[^"]*"'

# Call a tool (no auth)
curl -s -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_current_time","arguments":{"timezone":"Europe/Rome"}}}'

# MCP Inspector (interactive) — see "Connecting to the server" section above
```
