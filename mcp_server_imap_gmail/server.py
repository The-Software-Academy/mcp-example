import logging
from mcp.server import Server  # type: ignore[import-untyped]
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, EmailStr, ValidationError


class EmailCredentials(BaseModel):
    email: EmailStr
    imap_key: str


async def serve(email: str, imap_key: str) -> None:
    logger = logging.getLogger(__name__)

    try:
        credentials = EmailCredentials(email=email, imap_key=imap_key)
    except ValidationError as e:
        logger.error(f"Invalid email or IMAP key: {e}")
        return

    server: Server = Server("imap-gmail", version="0.1.0")

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
