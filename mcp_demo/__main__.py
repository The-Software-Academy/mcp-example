from mcp_demo import mcp
import click


@click.command()
@click.option("--transport", "-t", type=str, default="streamable-http", help="Transport protocol stdio, sse or streamable-http")
def main(transport):
    mcp.run(transport=transport)


main()
