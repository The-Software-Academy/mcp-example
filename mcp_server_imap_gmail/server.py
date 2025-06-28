from collections.abc import AsyncIterator
import contextlib
from datetime import datetime
from enum import Enum
import logging

import email as email_utils
from email.header import decode_header
import click
from imapclient import IMAPClient  # type: ignore[import-untyped]

from mcp.server import Server  # type: ignore[import-untyped]
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    Tool
)
from pydantic import BaseModel, EmailStr, ValidationError


from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


class GmailTools(str, Enum):
    LIST_EMAILS = "list_emails"


class GmailList(BaseModel):
    start_date: datetime
    end_date: datetime
    folder: str | None


class EmailCredentials(BaseModel):
    email: EmailStr
    imap_key: str


def extract_sender(msg) -> str:
    return msg.get("From", "")


def extract_subject(msg) -> str:
    subject, encoding = decode_header(msg.get("Subject", ""))[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding or "utf-8", errors="replace")
    return subject


def extract_text_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                charset = part.get_content_charset() or "utf-8"
                body = part.get_payload(decode=True).decode(
                    charset, errors="replace")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body = msg.get_payload(decode=True).decode(charset, errors="replace")
    return body.strip()


def list_emails(email: str, imap_key: str, folder: str, start_date: str, end_date: str) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}

    # Create SSL context - force fallback for dev container
    import ssl
    import os

    # Dev container environment - use fallback directly
    logger.warning("Using SSL fallback for dev container environment")
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    with IMAPClient(
        host="imap.gmail.com", port=993, use_uid=True, ssl=True, ssl_context=ssl_context
    ) as client:
        client.login(username=email, password=imap_key)
        client.select_folder(folder)
        search_criteria = f'SENTSINCE {datetime.fromisoformat(start_date).strftime("%d-%b-%Y")} BEFORE {datetime.fromisoformat(end_date).strftime("%d-%b-%Y")}'
        imap_ids = client.search(search_criteria)
        logger.info(
            f"Found {len(imap_ids)} emails matching criteria: {search_criteria}")
        for imap_id in imap_ids:
            data = client.fetch(imap_id, ["RFC822"])
            msg_bytes = data.get(imap_id, {}).get(b"RFC822")
            if not msg_bytes:
                continue
            msg = email_utils.message_from_bytes(msg_bytes)
            sender = extract_sender(msg)
            subject = extract_subject(msg)
            body = extract_text_body(msg)
            results[str(imap_id)] = dict(
                sender=sender,
                subject=subject,
                body=body
            )
    return results


@click.command()
@click.option("--email", "-e", type=str, help="Git repository path")
@click.option("--imap-key", "-k", type=str, help="IMAP key for authentication")
@click.option("--port", "-p", type=int, default=8000, help="Port to run the server on")
@click.option("-v", "--verbose", count=True)
def main(email: str, imap_key: str, port: int, verbose: bool) -> int:

    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    logging.basicConfig(level=logging_level)

    try:
        credentials = EmailCredentials(
            email=email, imap_key=imap_key
        )
    except ValidationError as e:
        logger.error(f"Invalid email or IMAP key: {e}")
        return 1

    app: Server = Server("imap-gmail", version="0.1.0")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=GmailTools.LIST_EMAILS,
                description="List emails in the user's Gmail account.",
                inputSchema=GmailList.model_json_schema()
            )
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> dict[str, dict[str, str]] | None:
        match name:
            case GmailTools.LIST_EMAILS:
                try:
                    emails = list_emails(
                        email=credentials.email,
                        imap_key=credentials.imap_key,
                        folder=arguments.get("folder", "INBOX"),
                        start_date=arguments.get(
                            "start_date", datetime.today()),
                        end_date=arguments.get("end_date", datetime.today())
                    )
                    return emails
                except Exception as e:
                    logger.error(f"Error listing emails: {e}")
                    return None
            case _:
                logger.error(f"Unknown tool called: {name}")
                raise ValueError(f"Unknown tool: {name}")
        return None

    # Create the session manager with our app and event store
    session_manager = StreamableHTTPSessionManager(
        app=app,
        json_response=True,
    )

    # ASGI handler for streamable HTTP connections
    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for managing session manager lifecycle."""
        async with session_manager.run():
            logger.info(
                "Application started with StreamableHTTP session manager!")
            try:
                yield
            finally:
                logger.info("Application shutting down...")

    # Create an ASGI application using the transport
    starlette_app = Starlette(
        debug=True,
        routes=[
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )

    import uvicorn

    uvicorn.run(starlette_app, host="127.0.0.1", port=port)

    return 0
