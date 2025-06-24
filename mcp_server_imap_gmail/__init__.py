import click
import logging
import sys
from .server import serve


@click.command()
@click.option("--email", "-e", type=str, help="Git repository path")
@click.option("--imap-key", "-k", type=str, help="IMAP key for authentication")
@click.option("-v", "--verbose", count=True)
def main(email: str, imap_key: str, verbose: bool) -> None:
    """MCP Git Server - Git functionality for MCP"""
    import asyncio

    logging_level = logging.WARN
    if verbose == 1:
        logging_level = logging.INFO
    elif verbose >= 2:
        logging_level = logging.DEBUG

    logging.basicConfig(level=logging_level, stream=sys.stderr)

    asyncio.run(serve(email, imap_key))


if __name__ == "__main__":
    main()
