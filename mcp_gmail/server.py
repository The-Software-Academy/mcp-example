"""Gmail MCP Server — complete showcase of MCP primitives.

Features demonstrated:
  ┌─ SECTION A: Demo tools (no credentials needed) ────────────────────┐
  │  get_current_time   — Pydantic return type, tool annotations        │
  │  create/get/list/delete_note — CRUD + lifespan shared state         │
  │  analyze_text       — structured Pydantic return                    │
  │  calculate          — safe AST eval, ToolError on bad input         │
  │  long_task          — progress reporting + client logging           │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ SECTION B: Gmail tools (require GMAIL_CREDENTIALS_PATH) ──────────┐
  │  list_emails        — Gmail query syntax, readOnlyHint annotation   │
  │  get_email / get_thread — full message content                      │
  │  list_labels        — all Gmail labels                              │
  │  mark_as_read       — bulk op with per-message client logging       │
  │  send_email         — elicitation confirmation before sending        │
  │  reply_to_email     — elicitation confirmation before sending        │
  │  summarize_email    — MCP sampling (delegates to host LLM)          │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ SECTION C: Resources ──────────────────────────────────────────────┐
  │  notes://           — static index resource                         │
  │  notes://{title}    — dynamic resource template                     │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ SECTION D: Prompts (5) ────────────────────────────────────────────┐
  │  triage_inbox  draft_reply  weekly_digest                           │
  │  brainstorm_with_notes  explain_mcp_pattern                         │
  └─────────────────────────────────────────────────────────────────────┘
  ┌─ Cross-cutting concerns ────────────────────────────────────────────┐
  │  MCP spec OAuth 2.0 (ConsentOAuthProvider + PersistentOAuthProvider)│
  │  /health, /auth-status, /auth, /oauth/consent custom routes         │
  │  StreamableHTTP stateful transport (stateless_http=False)           │
  └─────────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import operator
import os
import uuid
from string import Template
from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import asynccontextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pytz
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Gmail OAuth2 helper
# ──────────────────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def build_gmail_service() -> Any | None:
    """Return an authenticated Gmail API service, or None if credentials are missing/invalid.

    Loads a cached token from GMAIL_TOKEN_PERSISTENCY_PATH and auto-refreshes it.
    Never blocks on interactive I/O — call the gmail_authenticate tool to start
    a new OAuth2 consent flow when no valid token exists.
    """
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH")
    token_path = os.getenv("GMAIL_TOKEN_PERSISTENCY_PATH")

    if not credentials_path:
        logger.info("GMAIL_CREDENTIALS_PATH not set — Gmail tools unavailable. Call gmail_authenticate.")
        return None

    if not Path(credentials_path).exists():
        logger.warning("credentials.json not found at %s — see credentials/README.md.", credentials_path)
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        logger.error("Google auth packages not installed: %s", exc)
        return None

    creds: Any | None = None
    if token_path and Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if token_path:
                Path(token_path).parent.mkdir(parents=True, exist_ok=True)
                Path(token_path).write_text(creds.to_json())
            logger.info("Gmail token refreshed")
        else:
            logger.warning(
                "Gmail token missing or invalid. "
                "Call the gmail_authenticate tool to complete the OAuth2 consent flow."
            )
            return None

    service = build("gmail", "v1", credentials=creds)
    logger.info("Gmail API service ready")
    return service


# ──────────────────────────────────────────────────────────────────────────────
# MCP OAuth provider — consent page + optional disk persistence
# ──────────────────────────────────────────────────────────────────────────────

if _MCP_AUTH_ENABLED := bool(os.getenv("MCP_AUTH_TOKEN")):
    import secrets
    import time
    import urllib.parse
    from mcp.server.auth.provider import (
        AccessToken,
        AuthorizationCode,
        AuthorizationParams,
        RefreshToken,
        construct_redirect_uri,
    )
    from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

    class ConsentOAuthProvider(InMemoryOAuthProvider):
        """InMemoryOAuthProvider that redirects to a browser consent page before issuing codes."""

        def __init__(self, base_url: str) -> None:
            super().__init__(
                base_url=base_url,
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=["mcp:full"], default_scopes=["mcp:full"],
                ),
                revocation_options=RevocationOptions(enabled=True),
                required_scopes=["mcp:full"],
            )
            self._base_url = base_url.rstrip("/")
            self.pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}

        async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
            key = secrets.token_urlsafe(16)
            self.pending[key] = (client, params)
            return f"{self._base_url}/oauth/consent?key={urllib.parse.quote(key)}"

        def approve(self, key: str) -> str | None:
            item = self.pending.pop(key, None)
            if item is None:
                return None
            client, params = item
            scopes = params.scopes or []
            if client.scope:
                allowed = set(client.scope.split())
                scopes = [s for s in scopes if s in allowed]
            code_value = f"code_{secrets.token_hex(16)}"
            self.auth_codes[code_value] = AuthorizationCode(
                code=code_value,
                client_id=client.client_id or "",
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                scopes=scopes,
                expires_at=time.time() + 300,
                code_challenge=params.code_challenge,
            )
            return construct_redirect_uri(str(params.redirect_uri), code=code_value, state=params.state)

        def deny(self, key: str) -> str | None:
            item = self.pending.pop(key, None)
            if item is None:
                return None
            _, params = item
            return construct_redirect_uri(
                str(params.redirect_uri),
                error="access_denied",
                error_description="User denied access",
                state=params.state,
            )

    class PersistentOAuthProvider(ConsentOAuthProvider):
        """ConsentOAuthProvider that persists clients and tokens to a JSON file."""

        def __init__(self, base_url: str, state_path: Path) -> None:
            super().__init__(base_url=base_url)
            self._state_path = state_path
            self._load()

        def _load(self) -> None:
            if not self._state_path.exists():
                return
            try:
                data = json.loads(self._state_path.read_text())
                now = time.time()
                for cdata in data.get("clients", {}).values():
                    c = OAuthClientInformationFull.model_validate(cdata)
                    if c.client_id:
                        self.clients[c.client_id] = c
                for tdata in data.get("refresh_tokens", {}).values():
                    t = RefreshToken.model_validate(tdata)
                    self.refresh_tokens[t.token] = t
                for tdata in data.get("access_tokens", {}).values():
                    t = AccessToken.model_validate(tdata)
                    if t.expires_at is None or t.expires_at > now:
                        self.access_tokens[t.token] = t
                self._access_to_refresh_map.update(data.get("access_to_refresh", {}))
                self._refresh_to_access_map.update(data.get("refresh_to_access", {}))
                logger.info(
                    "OAuth state loaded from %s: %d client(s), %d access token(s), %d refresh token(s)",
                    self._state_path, len(self.clients), len(self.access_tokens), len(self.refresh_tokens),
                )
            except Exception as exc:
                logger.warning("OAuth state file %s unreadable (%s) — deleting and starting fresh", self._state_path, exc)
                try:
                    self._state_path.unlink()
                except OSError:
                    pass

        def _save(self) -> None:
            try:
                now = time.time()
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "version": 1,
                    "clients": {cid: c.model_dump(mode="json") for cid, c in self.clients.items()},
                    "refresh_tokens": {tok: t.model_dump(mode="json") for tok, t in self.refresh_tokens.items()},
                    "access_tokens": {
                        tok: t.model_dump(mode="json")
                        for tok, t in self.access_tokens.items()
                        if t.expires_at is None or t.expires_at > now
                    },
                    "access_to_refresh": dict(self._access_to_refresh_map),
                    "refresh_to_access": dict(self._refresh_to_access_map),
                }
                self._state_path.write_text(json.dumps(data, indent=2))
            except Exception as exc:
                logger.warning("Failed to save OAuth state: %s", exc)

        async def register_client(self, client_info: OAuthClientInformationFull) -> None:
            await super().register_client(client_info)
            self._save()

        async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
            token = await super().exchange_authorization_code(client, authorization_code)
            self._save()
            return token

        async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
            token = await super().exchange_refresh_token(client, refresh_token, scopes)
            self._save()
            return token

        async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
            await super().revoke_token(token)
            self._save()

    _OAUTH_STATE_PATH = Path(os.getenv("MCP_OAUTH_STATE_PATH") or (Path(__file__).parent.parent / "credentials" / "oauth_state.json"))
    _oauth_provider: PersistentOAuthProvider | None = PersistentOAuthProvider(
        base_url=f"http://localhost:{int(os.getenv('MCP_PORT', '8001'))}", state_path=_OAUTH_STATE_PATH
    )
else:
    _oauth_provider = None

# ──────────────────────────────────────────────────────────────────────────────
# Lifespan — shared state initialised once per server process
# fastmcp 3.x requires the lifespan to yield a dict[str, Any]
# Access in tools via: ctx.lifespan_context["key"]
# ──────────────────────────────────────────────────────────────────────────────

# Module-level reference to the lifespan dict — accessible from custom routes
# that don't have an MCP Context (e.g. /auth redirect).
_lifespan: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Initialise shared state and make it available to every tool handler."""
    logger.info("Server starting — initialising Gmail service…")
    gmail_service = build_gmail_service()
    if gmail_service is None:
        logger.info("Gmail not configured — demo tools only")
    else:
        logger.info("Gmail service initialised")
    _lifespan.clear()
    _lifespan.update({"gmail_service": gmail_service, "notes": {}})
    try:
        yield _lifespan
    finally:
        logger.info("Server shutting down")


# ──────────────────────────────────────────────────────────────────────────────
# FastMCP instance — auth is enabled when MCP_AUTH_TOKEN is set
#
# When enabled, FastMCP adds full MCP spec OAuth 2.0 automatically:
#   GET /.well-known/oauth-authorization-server  — discovery metadata
#   POST /register                               — dynamic client registration
#   GET  /authorize                              — redirects to /oauth/consent
#   POST /token                                  — code → access+refresh tokens
#   POST /revoke                                 — token revocation
#
# MCP clients (Claude Code, VS Code, MCP Inspector) discover and complete the
# flow automatically — no manual bearer-token configuration needed.
# ──────────────────────────────────────────────────────────────────────────────

_INSTRUCTIONS = (
    "This server provides Gmail management and general-purpose utilities. "
    "Gmail tools require GMAIL_CREDENTIALS_PATH to be set — they return a helpful "
    "error otherwise. Demo tools (notes, time, analyze, calculate, long_task) always work. "
    "Call list_emails before get_email — message IDs are not guessable. "
    "Call get_thread to read full conversation context before drafting a reply. "
    "send_email and reply_to_email ask for your confirmation before sending."
)

mcp = FastMCP(
    name="gmail-mcp",
    instructions=_INSTRUCTIONS,
    lifespan=lifespan,
    auth=_oauth_provider,
)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _require_gmail(ctx: Context) -> Any:
    """Return the Gmail service or raise an informative ToolError."""
    svc = ctx.lifespan_context.get("gmail_service")
    if svc is None:
        raise ToolError(
            "Gmail is not authenticated. Call the `gmail_authenticate` tool first to "
            "complete the OAuth2 flow, then retry this tool."
        )
    return svc


def _decode_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
    for part in payload.get("parts", []):
        body = _decode_body(part)
        if body:
            return body
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Elicitation helper — used by send_email and reply_to_email
# ──────────────────────────────────────────────────────────────────────────────

# Set MCP_ELICITATION_FAIL_OPEN=1 to proceed without confirmation when the client
# does not support elicitation (fail-open). Default is fail-closed (raise ToolError),
# which is the safe choice for destructive operations like sending email.
_ELICITATION_FAIL_OPEN = bool(os.getenv("MCP_ELICITATION_FAIL_OPEN"))


class _Confirm(BaseModel):
    confirm: bool = Field(default=False)  # optional — Accept/Decline carry the decision


async def _confirm_send(ctx: Context, message: str) -> bool:
    """Ask the user for confirmation using MCP elicitation.

    The MCP protocol carries the decision via result.action (accept/decline/cancel).
    The boolean field is required by the API but ignored — clicking Accept is enough.

    Behaviour when the client does not support elicitation is controlled by
    MCP_ELICITATION_FAIL_OPEN (default: fail-closed → ToolError).
    """
    try:
        result = await ctx.elicit(message, schema=_Confirm)
        return result.action == "accept"
    except Exception:
        if _ELICITATION_FAIL_OPEN:
            await ctx.warning(
                "Elicitation not supported by this client — proceeding without confirmation "
                "(MCP_ELICITATION_FAIL_OPEN is set). A production server should not allow this."
            )
            return True
        raise ToolError(
            "This action requires user confirmation via MCP elicitation, "
            "but the connected client does not support it. "
            "Set MCP_ELICITATION_FAIL_OPEN=1 to proceed without confirmation (not recommended for production)."
        )


def _send_message(svc: Any, msg: MIMEMultipart, thread_id: str | None = None) -> str:
    """Encode a MIME message, send it via Gmail API, and return the new message ID."""
    raw = urlsafe_b64encode(msg.as_bytes()).decode()
    body: dict[str, Any] = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    return svc.users().messages().send(userId="me", body=body).execute()["id"]


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic output models (give Claude fully-typed schemas to reason about)
# ──────────────────────────────────────────────────────────────────────────────

class TimeResult(BaseModel):
    timezone: str
    datetime_iso: str = Field(description="ISO-8601 with timezone offset")
    unix_timestamp: float
    day_of_week: str


class TextAnalysis(BaseModel):
    word_count: int
    char_count: int
    sentence_count: int
    avg_word_length: float
    language_hint: str = Field(description="'latin-script' or 'non-latin'")


class EmailSummary(BaseModel):
    id: str
    thread_id: str
    subject: str
    sender: str
    snippet: str
    thread_message_count: int = Field(1, description="Total number of messages in this thread.")


class EmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str
    sender: str
    recipient: str
    date: str
    snippet: str
    body: str
    label_ids: list[str]
    thread_message_count: int = Field(1, description="Total number of messages in this thread.")


class EmailList(BaseModel):
    messages: list[EmailSummary]
    total: int
    query: str
    next_page_token: str | None = Field(None, description="Pass to list_emails to fetch the next page")


class LabelInfo(BaseModel):
    id: str
    name: str
    label_type: str = Field(alias="type")
    model_config = {"populate_by_name": True}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — DEMO TOOLS (always available, no credentials needed)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def get_current_time(timezone: str = "UTC") -> TimeResult:
    """Get the current date and time in any IANA timezone.

    Examples: 'UTC', 'Europe/Rome', 'America/New_York', 'Asia/Tokyo'.
    """
    try:
        tz = pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        raise ToolError(
            f"Unknown timezone '{timezone}'. "
            "Use an IANA name such as 'Europe/Rome' or 'America/New_York'."
        )
    now = datetime.now(tz)
    return TimeResult(
        timezone=timezone,
        datetime_iso=now.isoformat(),
        unix_timestamp=now.timestamp(),
        day_of_week=now.strftime("%A"),
    )


@mcp.tool()
async def create_note(title: str, content: str, ctx: Context) -> str:
    """Create or overwrite an in-memory note. Notes persist for the server's lifetime.

    Use list_notes to see saved titles, get_note to read one back.
    """
    ctx.lifespan_context["notes"][title] = content
    await ctx.info(f"Note '{title}' saved ({len(content)} chars)")
    return f"Note '{title}' saved."


@mcp.tool(annotations={"readOnlyHint": True})
async def get_note(title: str, ctx: Context) -> str:
    """Retrieve an in-memory note by exact title."""
    notes: dict[str, str] = ctx.lifespan_context["notes"]
    if title not in notes:
        available = list(notes.keys())
        raise ToolError(
            f"Note '{title}' not found. "
            f"Available: {available if available else '(none yet)'}"
        )
    await ctx.debug(f"Read note '{title}' ({len(notes[title])} chars)")
    return notes[title]


@mcp.tool(annotations={"readOnlyHint": True})
async def list_notes(ctx: Context) -> list[str]:
    """List all in-memory note titles."""
    titles = list(ctx.lifespan_context["notes"].keys())
    await ctx.info(f"Notes store contains {len(titles)} note(s)")
    return titles


@mcp.tool(annotations={"destructiveHint": True})
async def delete_note(title: str, ctx: Context) -> str:
    """Delete an in-memory note by title."""
    notes: dict[str, str] = ctx.lifespan_context["notes"]
    if title not in notes:
        raise ToolError(f"Note '{title}' not found.")
    del notes[title]
    await ctx.info(f"Note '{title}' deleted")
    return f"Note '{title}' deleted."


@mcp.tool(annotations={"readOnlyHint": True})
def analyze_text(text: str) -> TextAnalysis:
    """Analyse text statistics: word count, char count, sentence count, avg word length."""
    import re

    words = text.split()
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    avg_len = round(sum(len(w) for w in words) / len(words), 2) if words else 0.0
    non_ascii = sum(1 for c in text if ord(c) > 127)
    lang = "non-latin" if text and non_ascii / len(text) > 0.3 else "latin-script"

    return TextAnalysis(
        word_count=len(words),
        char_count=len(text),
        sentence_count=len(sentences),
        avg_word_length=avg_len,
        language_hint=lang,
    )


_OPS: dict[type, Any] = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
}


def _safe_eval(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ToolError(
        "Unsupported expression. Only arithmetic (+, -, *, /, **, %, //) is allowed. "
        "Example: '(10 + 5) * 2 / 3'"
    )


@mcp.tool(annotations={"readOnlyHint": True})
def calculate(expression: str) -> float:
    """Safely evaluate an arithmetic expression. Supports +, -, *, /, **, %, //.

    Examples: '2 + 2', '10 / 3', '2 ** 10', '(100 - 5) * 1.5'
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        return _safe_eval(tree.body)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Cannot parse '{expression}': {exc}")


@mcp.tool()
async def long_task(steps: int, ctx: Context) -> str:
    """Simulate a long-running task with progress notifications and structured logging.

    Demonstrates MCP progress reporting (ctx.report_progress) and client logging
    (ctx.info, ctx.debug). Accepts 1–20 steps.
    """
    if not 1 <= steps <= 20:
        raise ToolError("steps must be between 1 and 20")

    await ctx.info(f"Starting long_task with {steps} steps")
    for i in range(steps):
        await ctx.report_progress(progress=i, total=steps)
        await ctx.debug(f"Processing step {i + 1}/{steps}")
        await asyncio.sleep(0.25)

    await ctx.report_progress(progress=steps, total=steps)
    await ctx.info(f"long_task complete — {steps} steps finished")
    return f"All {steps} steps completed."


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A½ — AUTHENTICATION TOOL
# ══════════════════════════════════════════════════════════════════════════════

# Shared state between the `authenticate` tool call and the /oauth/callback route.
# The tool blocks on `future`; the route handler sets the result when Google redirects back.
_pending_oauth: dict[str, Any] = {}

_TEMPLATES = Path(__file__).parent / "templates"
_OAUTH_SUCCESS_HTML = (_TEMPLATES / "gmail_success.html").read_text()


@mcp.tool(annotations={"destructiveHint": True})
async def gmail_logout(ctx: Context) -> str:
    """Clear Gmail authentication from memory and disk.

    Removes the cached token from both the server's in-memory state and
    token.json on disk. After calling this, gmail_authenticate is required
    before any Gmail tool will work again. Useful for switching accounts or
    testing the authentication flow.
    """
    ctx.lifespan_context["gmail_service"] = None
    token_path = os.getenv("GMAIL_TOKEN_PERSISTENCY_PATH")
    if token_path and Path(token_path).exists():
        Path(token_path).unlink()
        return "Gmail credentials cleared from memory and disk. Call gmail_authenticate to re-authenticate."
    return "Gmail credentials cleared from memory. Call gmail_authenticate to re-authenticate."


@mcp.tool()
async def gmail_authenticate(ctx: Context) -> str:
    """Authenticate with Gmail via OAuth2.

    Returns a Google consent URL. Open it in your browser and grant access —
    the server hot-reloads Gmail automatically when the callback fires.
    No server restart or second call needed.

    If Gmail is already authenticated, returns the current account email.
    Call this first if any Gmail tool returns an authentication error.
    """
    svc = ctx.lifespan_context.get("gmail_service")
    if svc is not None:
        try:
            profile = svc.users().getProfile(userId="me").execute()
            return f"Already authenticated as **{profile['emailAddress']}**. Gmail tools are ready."
        except Exception:
            pass  # Token revoked — fall through to re-authenticate

    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH")
    token_path = os.getenv("GMAIL_TOKEN_PERSISTENCY_PATH")

    if not credentials_path or not Path(credentials_path).exists():
        raise ToolError(
            "GMAIL_CREDENTIALS_PATH is not set or the file does not exist. "
            "See credentials/README.md for setup instructions."
        )

    # If a persisted token exists on disk, load it now.
    if token_path and Path(token_path).exists():
        svc = build_gmail_service()
        if svc is not None:
            ctx.lifespan_context["gmail_service"] = svc
            profile = svc.users().getProfile(userId="me").execute()
            return f"Authenticated as **{profile['emailAddress']}**. All Gmail tools are now ready."

    port = int(os.getenv("MCP_PORT", "8001"))
    auth_url = f"http://localhost:{port}/auth"
    elicitation_id = str(uuid.uuid4())

    # Store session + elicitation_id so /oauth/callback can send the completion
    # notification back through the GET SSE channel when OAuth finishes.
    _pending_oauth["session"] = ctx.request_context.session
    _pending_oauth["elicitation_id"] = elicitation_id

    try:
        result = await ctx.elicit_url(
            message=(
                "Gmail authentication required. "
                "Click Accept to open the Google OAuth consent page. "
                "Gmail tools will activate automatically once you grant access."
            ),
            url=auth_url,
            elicitation_id=elicitation_id,
        )
        if result.action == "accept":
            return (
                "OAuth flow started — complete the authorization in your browser. "
                "Gmail tools will be available automatically when done."
            )
        return "Authentication cancelled."
    except Exception:
        # Fallback for clients that don't support URL-mode elicitation (spec 2025-11-25)
        return (
            f"**Open this URL in your browser to grant Gmail access:**\n\n"
            f"{auth_url}\n\n"
            f"Gmail tools will activate automatically when you complete the flow."
        )


@mcp.custom_route("/oauth/callback", methods=["GET"])
async def oauth_callback(request: Request) -> Response:
    """Receive the Google OAuth2 redirect and exchange the code for a token."""
    if not _pending_oauth or "flow" not in _pending_oauth:
        return Response(
            "<html><body>No pending OAuth flow — call the <code>authenticate</code> "
            "tool first.</body></html>",
            media_type="text/html",
            status_code=400,
        )

    flow: Any = _pending_oauth["flow"]
    token_path: str = _pending_oauth["token_path"]
    lifespan_ctx: dict = _pending_oauth["lifespan"]

    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as exc:
        logger.error("OAuth token exchange failed: %s", exc)
        return Response(f"Authentication failed: {exc}", status_code=500)
    finally:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

    # Persist token to disk only if GMAIL_TOKEN_PERSISTENCY_PATH is explicitly set
    if token_path:
        p = Path(token_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(flow.credentials.to_json())

    # Hot-reload Gmail service into shared lifespan — no server restart needed
    from googleapiclient.discovery import build
    svc = build("gmail", "v1", credentials=flow.credentials)
    lifespan_ctx["gmail_service"] = svc
    profile = svc.users().getProfile(userId="me").execute()
    email = profile["emailAddress"]
    logger.info("Gmail authenticated as %s", email)

    # Notify the MCP client that the out-of-band elicitation is complete.
    # This uses the persistent GET SSE channel (stateful mode) — the spec-native
    # way to tell the client "the thing you were waiting for outside MCP is done".
    session = _pending_oauth.get("session")
    elicitation_id = _pending_oauth.get("elicitation_id")
    if session and elicitation_id:
        try:
            await session.send_elicit_complete(elicitation_id)
            logger.info("Sent notifications/elicitation/complete for %s", elicitation_id)
        except Exception as exc:
            logger.warning("Could not send elicitation/complete: %s", exc)

    _pending_oauth.clear()

    return Response(_OAUTH_SUCCESS_HTML, media_type="text/html")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — GMAIL TOOLS (require GMAIL_CREDENTIALS_PATH)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
async def list_emails(
    ctx: Context,
    query: str = "is:unread",
    max_results: int = 10,
    page_token: str | None = None,
) -> EmailList:
    """List Gmail messages matching a search query. Uses Gmail search syntax.

    Supports pagination: if next_page_token is present in the result, pass it
    back as page_token to fetch the next page of results.

    Query examples:
      'is:unread'                        — unread messages
      'from:boss@example.com'            — from a specific sender
      'subject:invoice after:2025/01/01' — invoices from 2025
      'label:INBOX has:attachment'       — inbox with attachments

    Call this before get_email — message IDs are not guessable.
    """
    svc = _require_gmail(ctx)
    page_info = f" (page_token={page_token[:8]}…)" if page_token else ""
    await ctx.info(f"Searching Gmail: query='{query}' max={max_results}{page_info}")

    kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        kwargs["pageToken"] = page_token

    result = svc.users().messages().list(**kwargs).execute()
    raw = result.get("messages", [])
    next_token: str | None = result.get("nextPageToken")

    await ctx.debug(f"Found {len(raw)} message(s){', more available' if next_token else ''}")
    summaries: list[EmailSummary] = []

    # Fetch thread sizes for unique thread IDs in this result set (one call per thread, not per message).
    thread_counts: dict[str, int] = {}
    for tid in {item["threadId"] for item in raw}:
        t = svc.users().threads().get(userId="me", id=tid, format="metadata").execute()
        thread_counts[tid] = len(t.get("messages", []))

    for item in raw:
        msg = svc.users().messages().get(
            userId="me", id=item["id"], format="metadata",
            metadataHeaders=["Subject", "From"],
        ).execute()
        hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        summaries.append(EmailSummary(
            id=item["id"],
            thread_id=item["threadId"],
            subject=hdrs.get("Subject", "(no subject)"),
            sender=hdrs.get("From", ""),
            snippet=msg.get("snippet", ""),
            thread_message_count=thread_counts.get(item["threadId"], 1),
        ))

    await ctx.info(f"Returned {len(summaries)} message(s){', next_page_token available' if next_token else ''}")
    return EmailList(messages=summaries, total=len(summaries), query=query, next_page_token=next_token)


# ──────────────────────────────────────────────────────────────────────────────
# Streaming inbox tools — two complementary patterns:
#
#   stream_inbox  — fetches existing unread emails with progress notifications
#                   (demonstrates notifications/progress + notifications/message)
#   watch_inbox   — polls for NEW arriving messages after stream starts
#                   (demonstrates long-running tools + cooperative cancellation)
#
# Best experienced in MCP Inspector where notifications appear in real time.
# ──────────────────────────────────────────────────────────────────────────────

_stream_control: dict[str, bool] = {"stop": False}


@mcp.tool(annotations={"readOnlyHint": True})
async def stream_inbox(ctx: Context, max_emails: int = 20) -> EmailList:
    """Fetch existing unread inbox emails one by one, emitting a progress notification per email.

    Demonstrates MCP progress reporting (notifications/progress) and per-item
    logging (notifications/message). Best viewed in MCP Inspector where both
    notification types appear in real time as each email is fetched.

    Returns the full list when complete. Use watch_inbox to monitor for NEW arrivals.

    Args:
        max_emails: Number of unread emails to fetch (1–50, default 20).
    """
    if not 1 <= max_emails <= 50:
        raise ToolError("max_emails must be between 1 and 50")

    svc = _require_gmail(ctx)

    result = svc.users().messages().list(
        userId="me", q="is:unread in:inbox", maxResults=max_emails
    ).execute()
    ids = [m["id"] for m in result.get("messages", [])]
    total = len(ids)

    await ctx.info(f"Found {total} unread message(s) — fetching with progress…")

    summaries: list[EmailSummary] = []
    try:
        for i, msg_id in enumerate(ids):
            msg = svc.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["Subject", "From"],
            ).execute()
            hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = hdrs.get("Subject", "(no subject)")
            sender = hdrs.get("From", "")
            snippet = msg.get("snippet", "")

            await ctx.report_progress(
                progress=i + 1,
                total=total,
                message=f"[{i+1}/{total}] {sender} — {subject}",
            )
            await ctx.info(f"[{i+1}/{total}] 📧 {sender}\n  {subject}\n  {snippet[:120]}")

            summaries.append(EmailSummary(
                id=msg_id,
                thread_id=msg["threadId"],
                subject=subject,
                sender=sender,
                snippet=snippet,
            ))

    except asyncio.CancelledError:
        await ctx.warning(f"Stream cancelled by client after {len(summaries)} of {total} emails.")
        raise

    return EmailList(messages=summaries, total=len(summaries), query="is:unread in:inbox")


@mcp.tool(annotations={"readOnlyHint": True})
async def watch_inbox(
    ctx: Context,
    poll_interval: int = 15,
    max_duration: int = 300,
) -> str:
    """⚠️ ANTIPATTERN SHOWCASE — Poll inbox for new messages and emit a log notification per arrival.

    This tool intentionally demonstrates what NOT to do in MCP.

    WHY THIS IS AN ANTIPATTERN:
      The MCP protocol defines notifications/message (logging) and notifications/progress
      strictly as utility events tied to an in-flight request — they exist to report progress
      while processing a chunk of work, not as a persistent streaming channel.

      A blocking long-running tool is NOT the right model for "notify me when something
      happens". MCP has no pub/sub or persistent push mechanism. The correct pattern for
      event-driven notifications is an external system (webhooks, push notifications, etc.)
      that integrates with the host application, not a blocking MCP tool call.

    PRACTICAL CONSEQUENCE:
      MCP clients impose a request timeout (MCP Inspector: 60s, Claude Code: configurable).
      This tool will be cancelled by the client before it can deliver meaningful results
      in short-timeout environments.

    WHEN IT DOES WORK:
      In clients with long or no request timeouts (Claude Code with a high timeout configured),
      the blocking design functions correctly and log notifications arrive in real time.
      stop_watch_inbox() sets a cooperative stop flag checked between poll cycles.

    Args:
        poll_interval: Seconds between Gmail polls (5–60, default 15).
        max_duration:  Maximum seconds before auto-stopping (default 300 = 5 min).
    """
    if not 5 <= poll_interval <= 60:
        raise ToolError("poll_interval must be between 5 and 60 seconds")
    if not 10 <= max_duration <= 3600:
        raise ToolError("max_duration must be between 10 and 3600 seconds")

    svc = _require_gmail(ctx)
    _stream_control["stop"] = False

    # Snapshot current inbox as baseline — only arrivals after this point are reported
    result = svc.users().messages().list(userId="me", q="in:inbox", maxResults=100).execute()
    known_ids: set[str] = {m["id"] for m in result.get("messages", [])}

    await ctx.info(
        f"👀 Watching inbox for new messages… "
        f"(baseline: {len(known_ids)} messages, polling every {poll_interval}s, "
        f"max {max_duration}s). Call stop_watch_inbox() to stop early."
    )

    new_count = 0
    elapsed = 0

    try:
        while elapsed < max_duration:
            if _stream_control["stop"]:
                await ctx.info(f"Stopped by stop_watch_inbox(). {new_count} new message(s) detected.")
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            result = svc.users().messages().list(
                userId="me", q="in:inbox", maxResults=100
            ).execute()
            current_ids: set[str] = {m["id"] for m in result.get("messages", [])}
            new_ids = current_ids - known_ids

            for msg_id in new_ids:
                msg = svc.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["Subject", "From"],
                ).execute()
                hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = hdrs.get("Subject", "(no subject)")
                sender = hdrs.get("From", "")
                snippet = msg.get("snippet", "")
                new_count += 1

                await ctx.info(f"📬 New message #{new_count}\nFrom: {sender}\nSubject: {subject}\n{snippet[:120]}")

            known_ids = current_ids

    except asyncio.CancelledError:
        await ctx.warning(f"Stream cancelled by client. {new_count} new message(s) detected.")
        raise

    return f"Stream ended after {elapsed}s. Detected {new_count} new message(s)."


@mcp.tool()
async def stop_watch_inbox(ctx: Context) -> str:
    """Stop a running watch_inbox after its current poll cycle completes."""
    if not _stream_control.get("stop", True):
        _stream_control["stop"] = True
        return "Stop signal sent — watch will halt after the current poll cycle."
    return "No watch is currently running."


@mcp.tool(annotations={"readOnlyHint": True})
async def get_email(message_id: str, ctx: Context) -> EmailMessage:
    """Get full content of a single Gmail message including body and all headers.

    Get the message_id from list_emails first.
    """
    svc = _require_gmail(ctx)
    await ctx.debug(f"Fetching message {message_id}")

    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload", {})
    hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}

    thread = svc.users().threads().get(userId="me", id=msg["threadId"], format="metadata").execute()

    return EmailMessage(
        id=msg["id"],
        thread_id=msg["threadId"],
        subject=hdrs.get("Subject", "(no subject)"),
        sender=hdrs.get("From", ""),
        recipient=hdrs.get("To", ""),
        date=hdrs.get("Date", ""),
        snippet=msg.get("snippet", ""),
        body=_decode_body(payload),
        label_ids=msg.get("labelIds", []),
        thread_message_count=len(thread.get("messages", [])),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_thread(thread_id: str, ctx: Context) -> list[EmailMessage]:
    """Get all messages in a Gmail thread, oldest first.

    Use this to read full conversation context before drafting a reply.
    """
    svc = _require_gmail(ctx)
    await ctx.debug(f"Fetching thread {thread_id}")

    thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
    messages: list[EmailMessage] = []
    for msg in thread.get("messages", []):
        payload = msg.get("payload", {})
        hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}
        messages.append(EmailMessage(
            id=msg["id"],
            thread_id=msg["threadId"],
            subject=hdrs.get("Subject", "(no subject)"),
            sender=hdrs.get("From", ""),
            recipient=hdrs.get("To", ""),
            date=hdrs.get("Date", ""),
            snippet=msg.get("snippet", ""),
            body=_decode_body(payload),
            label_ids=msg.get("labelIds", []),
        ))

    await ctx.info(f"Thread {thread_id} contains {len(messages)} message(s)")
    return messages


@mcp.tool(annotations={"readOnlyHint": True})
async def list_labels(ctx: Context) -> list[LabelInfo]:
    """List all Gmail labels: system labels (INBOX, SENT, etc.) and custom labels."""
    svc = _require_gmail(ctx)
    result = svc.users().labels().list(userId="me").execute()
    labels = [
        LabelInfo(id=lbl["id"], name=lbl["name"], type=lbl.get("type", "user"))
        for lbl in result.get("labels", [])
    ]
    await ctx.info(f"Found {len(labels)} label(s)")
    return labels


@mcp.tool(annotations={"destructiveHint": True})
async def mark_as_read(message_ids: list[str], ctx: Context) -> str:
    """Mark one or more Gmail messages as read (removes the UNREAD label).

    Logs each message processed so you can track bulk operation progress.
    """
    svc = _require_gmail(ctx)
    total = len(message_ids)
    await ctx.info(f"Marking {total} message(s) as read")

    for i, msg_id in enumerate(message_ids):
        svc.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        await ctx.report_progress(progress=i + 1, total=total)
        await ctx.debug(f"Marked message {i + 1}/{total} ({msg_id}) as read")

    await ctx.info(f"Bulk mark-as-read complete: {total} message(s) processed")
    return f"Marked {total} message(s) as read."


@mcp.tool()
async def send_email(to: str, subject: str, body: str, ctx: Context, cc: str = "") -> str:
    """Compose and send a new Gmail message. Asks for elicitation confirmation before sending.

    Args:
        to:      Recipient address
        subject: Email subject line
        body:    Plain-text body
        cc:      Optional CC address(es), comma-separated
    """
    svc = _require_gmail(ctx)

    if not await _confirm_send(ctx, f"Send email to {to!r} with subject {subject!r}?"):
        return "Email sending cancelled."

    msg = MIMEMultipart()
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(body, "plain"))

    await ctx.info(f"Sending to {to!r} — subject: {subject!r}")
    msg_id = _send_message(svc, msg)
    await ctx.info(f"Sent — ID: {msg_id}")
    return f"Email sent to {to}. Message ID: {msg_id}"


@mcp.tool()
async def reply_to_email(message_id: str, body: str, ctx: Context) -> str:
    """Reply to an existing Gmail message, preserving thread context.

    Sets In-Reply-To and References headers for correct threading.
    Asks for elicitation confirmation before sending.
    Get the message_id from list_emails or get_thread first.
    """
    svc = _require_gmail(ctx)

    orig = svc.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["Subject", "From", "Message-ID", "References"],
    ).execute()
    hdrs = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}

    if not await _confirm_send(ctx, f"Reply to {hdrs.get('From', message_id)!r} on '{hdrs.get('Subject', '')}'?"):
        return "Reply cancelled."

    subject = hdrs.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    reply = MIMEMultipart()
    reply["to"] = hdrs.get("From", "")
    reply["subject"] = subject
    reply["In-Reply-To"] = hdrs.get("Message-ID", "")
    mid = hdrs.get("Message-ID", "")
    refs = hdrs.get("References", "")
    reply["References"] = f"{refs} {mid}".strip()
    reply.attach(MIMEText(body, "plain"))

    await ctx.info(f"Sending reply to thread {orig['threadId']}")
    msg_id = _send_message(svc, reply, thread_id=orig["threadId"])
    await ctx.info(f"Reply sent — ID: {msg_id}")
    return f"Reply sent. Message ID: {msg_id}"


@mcp.tool(annotations={"readOnlyHint": True})
async def summarize_email(message_id: str, ctx: Context) -> str:
    """Summarise a Gmail message using the host LLM via MCP sampling.

    This tool delegates to the client agent (Claude) for summarisation — no
    external API key is required. If the client does not support sampling,
    a clear error is returned.
    """
    svc = _require_gmail(ctx)
    await ctx.debug(f"Fetching message {message_id} for summarisation")

    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload", {})
    hdrs = {h["name"]: h["value"] for h in payload.get("headers", [])}
    body = _decode_body(payload)
    subject = hdrs.get("Subject", "(no subject)")
    sender = hdrs.get("From", "unknown")

    prompt = (
        f"Summarise this email in ≤150 words. Focus on key points, "
        f"required actions, and any deadlines.\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{body[:3000]}"
    )

    await ctx.report_progress(progress=0, total=2)
    await ctx.info(f"Requesting summary via MCP sampling for message {message_id}")

    try:
        result = await ctx.sample(prompt)
        await ctx.report_progress(progress=2, total=2)
        await ctx.info("Summary complete")
        return result.text  # type: ignore[union-attr]
    except Exception as exc:
        raise ToolError(
            f"summarize_email requires MCP sampling support from the client. "
            f"The connected client does not support it ({type(exc).__name__}). "
            "Try Claude Code ≥2.1.76 or another sampling-capable MCP host."
        ) from exc


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C — RESOURCES
# ══════════════════════════════════════════════════════════════════════════════

@mcp.resource("notes://", title="Notes Index")
async def notes_index(ctx: Context) -> str:
    """Index of all note titles stored in this session. One title per line."""
    titles = list(ctx.lifespan_context["notes"].keys())
    return "\n".join(titles) if titles else "(no notes yet — use create_note to add one)"


@mcp.resource("notes://{title}", title="Note Contents")
async def note_content(title: str, ctx: Context) -> str:
    """Content of a specific note. Discover titles first via the 'notes://' resource."""
    notes: dict[str, str] = ctx.lifespan_context["notes"]
    return notes.get(title, f"Note '{title}' not found.")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION D — PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

_GMAIL_AUTH_PREAMBLE = (
    "IMPORTANT — Before calling any Gmail tool, call `gmail_authenticate` first. "
    "If it returns an authentication URL, stop and tell the user to open it in their browser. "
    "Only proceed with the task after `gmail_authenticate` confirms a successful login.\n\n"
)


@mcp.prompt()
def triage_inbox(date_range: str = "today") -> str:
    """Prioritise inbox emails with urgency labels, deadlines, and suggested replies."""
    return (
        _GMAIL_AUTH_PREAMBLE
        + f"Review my Gmail inbox for {date_range} using list_emails with "
        f'query="after:{date_range}". '
        "For each email assign:\n"
        "  🔴 Urgent — needs reply today\n"
        "  🟡 Actionable — reply within a week\n"
        "  🟢 FYI — no reply needed\n"
        "  ⚫ Noise — can be archived\n\n"
        "Extract any deadlines or commitments. "
        "For urgent emails, draft a one-sentence suggested reply. "
        "Output as a markdown table: From | Subject | Priority | Deadline | Suggested Reply."
    )


@mcp.prompt()
def draft_reply(message_id: str, tone: str = "professional") -> str:
    """Draft a contextual reply after reading the full conversation thread."""
    return (
        _GMAIL_AUTH_PREAMBLE
        + f"Fetch the email thread for message {message_id}:\n"
        f"1. Call get_email('{message_id}') to read the message\n"
        "2. Call get_thread with the returned thread_id\n\n"
        f"Draft a {tone} reply that:\n"
        "  - Acknowledges the sender's key points\n"
        "  - Answers every question clearly\n"
        "  - Closes with a specific next step\n\n"
        "Keep it under 200 words. Show the draft and call reply_to_email only after my confirmation."
    )


@mcp.prompt()
def weekly_digest(week_of: str) -> str:
    """Generate a weekly email report grouped by sender domain and urgency."""
    return (
        _GMAIL_AUTH_PREAMBLE
        + f'Search Gmail for the week of {week_of} with list_emails query="after:{week_of}". '
        "Group by sender domain. Identify:\n"
        "  1. Action items with deadlines\n"
        "  2. Threads where I haven't replied yet\n"
        "  3. Newsletters and automated emails to archive\n\n"
        "Output a markdown report with a short executive summary at the top."
    )


@mcp.prompt()
def brainstorm_with_notes(topic: str) -> str:
    """Use the notes system to capture and organise brainstorming output."""
    return (
        f"Help me brainstorm '{topic}'.\n"
        "1. Call list_notes() to check for existing context\n"
        "2. Generate 7 distinct ideas\n"
        "3. Save each as create_note('idea-{topic}-N', ...) for N=1..7\n"
        "4. Synthesise into a plan saved as create_note('plan-{topic}', ...)\n"
        "5. Show me the final plan"
    )


@mcp.prompt()
def explain_mcp_pattern(pattern: str = "lifespan") -> str:
    """Explain an MCP pattern live using this server as a hands-on example."""
    return (
        f"Explain the MCP '{pattern}' pattern using this server as a live demo.\n\n"
        "Structure:\n"
        "1. What it is and why it exists in the MCP spec\n"
        "2. How it's coded in this server\n"
        "3. Live demo — call the relevant tool or read a resource\n"
        "4. When to use it vs. alternatives\n\n"
        "Patterns available: lifespan, context-injection, resources, prompts, "
        "bearer-auth, progress-reporting, error-handling, pydantic-returns, "
        "tool-annotations, elicitation, sampling, graceful-degradation."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Custom routes — registered on the mcp instance, served alongside /mcp
# ══════════════════════════════════════════════════════════════════════════════

@mcp.custom_route("/auth", methods=["GET"])
async def auth_redirect(request: Request) -> Response:
    """Redirect browser directly to the Google OAuth consent page.

    Simpler than copying the full URL — just open http://localhost:8001/auth.
    Requires GMAIL_CREDENTIALS_PATH to be set.
    """
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH")
    token_path = os.getenv("GMAIL_TOKEN_PERSISTENCY_PATH")
    if not credentials_path or not Path(credentials_path).exists():
        return Response("GMAIL_CREDENTIALS_PATH not set or file not found.", status_code=500)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        credentials_path, SCOPES, autogenerate_code_verifier=False
    )
    flow.redirect_uri = str(request.url).replace("/auth", "/oauth/callback").split("?")[0]
    auth_url, _ = flow.authorization_url(access_type="offline")

    # Preserve session + elicitation_id set by gmail_authenticate (if called via tool)
    session = _pending_oauth.get("session")
    elicitation_id = _pending_oauth.get("elicitation_id")
    _pending_oauth.clear()
    _pending_oauth.update({
        "flow": flow,
        "token_path": token_path,
        "lifespan": _lifespan,
        "session": session,
        "elicitation_id": elicitation_id,
    })

    return RedirectResponse(auth_url)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.2.0"})


@mcp.custom_route("/auth-status", methods=["GET"])
async def auth_status(request: Request) -> JSONResponse:
    return JSONResponse({
        "gmail_configured": os.getenv("GMAIL_CREDENTIALS_PATH") is not None,
        "mcp_auth_enabled": _MCP_AUTH_ENABLED,
    })


# ══════════════════════════════════════════════════════════════════════════════
# MCP OAuth consent page
# Shown when an MCP client redirects the user's browser to /authorize.
# ConsentOAuthProvider.authorize() redirects here; the Approve/Deny buttons
# POST back to complete or cancel the flow.  These routes are @custom_route
# so they bypass RequireAuthMiddleware — the user isn't authenticated yet.
# ══════════════════════════════════════════════════════════════════════════════

_CONSENT_HTML = (_TEMPLATES / "consent.html").read_text()
_CONSENT_EXPIRED_HTML = (_TEMPLATES / "consent_expired.html").read_text()


@mcp.custom_route("/oauth/consent", methods=["GET"])
async def oauth_consent_page(request: Request) -> Response:
    """Render the OAuth consent page for MCP client authorisation."""
    if _oauth_provider is None:
        return Response("MCP auth is not enabled.", status_code=404)
    key = request.query_params.get("key", "")
    item = _oauth_provider.pending.get(key)
    if item is None:
        return Response(_CONSENT_EXPIRED_HTML, media_type="text/html", status_code=400)
    client, params = item
    client_name = client.client_name or client.client_id or "Unknown client"
    scopes = params.scopes or ["mcp:full"]
    return Response(_render_consent(key, client_name, scopes), media_type="text/html")


def _render_consent(key: str, client_name: str, scopes: list[str], error: str = "") -> str:
    scope_items = "".join(f"<li>{s}</li>" for s in scopes)
    error_html = f'<p class="error">{error}</p>' if error else ""
    return Template(_CONSENT_HTML).substitute(key=key, client_name=client_name, scope_items=scope_items, error_html=error_html)


@mcp.custom_route("/oauth/consent", methods=["POST"])
async def oauth_consent_action(request: Request) -> Response:
    """Handle Approve or Deny from the consent page."""
    if _oauth_provider is None:
        return Response("MCP auth is not enabled.", status_code=404)
    form = await request.form()
    key = str(form.get("key", ""))
    action = str(form.get("action", "deny"))

    if action == "deny":
        redirect_url = _oauth_provider.deny(key)
        if redirect_url is None:
            return Response(_CONSENT_EXPIRED_HTML, media_type="text/html", status_code=400)
        return RedirectResponse(redirect_url, status_code=302)

    # Validate the server password before approving.
    entered = str(form.get("token", ""))
    expected = os.getenv("MCP_AUTH_TOKEN", "")
    if entered != expected:
        item = _oauth_provider.pending.get(key)
        if item is None:
            return Response(_CONSENT_EXPIRED_HTML, media_type="text/html", status_code=400)
        client, params = item
        client_name = client.client_name or client.client_id or "Unknown client"
        scopes = params.scopes or ["mcp:full"]
        html = _render_consent(key, client_name, scopes, error="Incorrect password. Try again.")
        return Response(html, media_type="text/html", status_code=401)

    redirect_url = _oauth_provider.approve(key)
    if redirect_url is None:
        return Response(_CONSENT_EXPIRED_HTML, media_type="text/html", status_code=400)
    return RedirectResponse(redirect_url, status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# App factory — called from __main__.py
# ══════════════════════════════════════════════════════════════════════════════

class _RegistrationCompatMiddleware:
    """Normalize POST /register for clients that omit refresh_token from grant_types.

    The MCP SDK requires both authorization_code and refresh_token. Some clients
    (e.g. Antigravity) only send authorization_code. This patches the body
    transparently before the SDK handler validates it.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/register" and scope.get("method") == "POST":
            chunks: list[bytes] = []
            more = True
            while more:
                msg = await receive()
                chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
            body = b"".join(chunks)

            try:
                data = json.loads(body)
                logger.debug("POST /register body: %s", json.dumps(data))
                grant_types: list[str] = data.get("grant_types", ["authorization_code", "refresh_token"])
                if isinstance(grant_types, list) and "authorization_code" in grant_types and "refresh_token" not in grant_types:
                    data["grant_types"] = grant_types + ["refresh_token"]
                    body = json.dumps(data).encode()
                    logger.info("RegistrationCompat: added refresh_token to grant_types for client %r",
                                data.get("client_name", "<unknown>"))
            except (json.JSONDecodeError, TypeError):
                pass

            delivered = False

            async def patched_receive() -> Any:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            await self._app(scope, patched_receive, send)
        else:
            await self._app(scope, receive, send)


def build_app():
    """Build and return the ASGI application.

    MCP endpoint:    /mcp    (StreamableHTTP, stateful — GET SSE for notifications)
    OAuth endpoints: /.well-known/oauth-authorization-server, /authorize, /token, ...
    Consent page:    /oauth/consent   (open, no auth required)
    Health check:    /health          (open, no auth required)

    Auth is enabled when MCP_AUTH_TOKEN is set at process start.
    FastMCP handles all OAuth plumbing automatically via ConsentOAuthProvider.
    """
    app = mcp.http_app(path="/mcp", stateless_http=False)
    # Allow cross-origin requests from browser-based MCP clients.
    # - antigravity.google: Antigravity IDE (browser-based, makes XHR to /token)
    # - localhost / 127.0.0.1 any port: MCP Inspector and other local dev tools
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://antigravity.google"],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "mcp-session-id"],
        expose_headers=["mcp-session-id"],
    )
    if _MCP_AUTH_ENABLED:
        app = _RegistrationCompatMiddleware(app)
    return app
