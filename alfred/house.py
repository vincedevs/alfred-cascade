"""Wayne Manor: the virtual smart home Alfred controls by voice (lights, temperature, the
desk telephone and Spotify), through its MCP server.

voice.py adds these tools to Alfred's at the start of every conversation. They're
listed afresh each time because the music tools only exist while Spotify is enabled. If
the house can't be reached, that conversation simply goes on without it.

Pipecat's MCP client does the work: it turns Wayne Manor's tools into Alfred's tools, runs
them when the model calls them, reconnects if Wayne Manor restarts, and closes with the
session. Wayne Manor's answers are sentences written to be spoken, refusals included.
"""

import asyncio
from functools import partial

import pipecat.services.mcp_service as pipecat_mcp
from loguru import logger
from mcp import ClientSession, Implementation
from mcp.client.session_group import StreamableHttpParameters

from alfred.config import Config

CONNECT_SECONDS = 3  # Alfred doesn't wait longer than this for a missing house
CALL_SECONDS = 20  # play_music can take ~15 s while Spotify opens
ALFRED = Implementation(name="Alfred", version="2")  # how MCP servers see Alfred


async def wayne_manor_tools(config: Config) -> list:
    """Wayne Manor's tools, ready for Alfred's model. Empty if there's no house set up, or it
    can't be reached within CONNECT_SECONDS."""
    if not config.wayne_manor_url:
        logger.info("No house: ALFRED_WAYNE_MANOR_URL isn't set")
        return []
    connect_as_alfred()
    house = pipecat_mcp.MCPClient(
        StreamableHttpParameters(
            url=config.wayne_manor_url,
            timeout=CONNECT_SECONDS,
            sse_read_timeout=CALL_SECONDS,
        )
    )
    try:
        async with asyncio.timeout(CONNECT_SECONDS):
            tools = (await house.tools()).standard_tools
    except Exception as error:
        logger.warning(f"Wayne Manor unreachable ({type(error).__name__}): no house this time")
        await house.close()
        return []
    logger.debug(f"Wayne Manor tools: {', '.join(tool.name for tool in tools)}")
    return tools


def connect_as_alfred() -> None:
    """Wayne Manor's activity feed shows each caller's name, but Pipecat's MCP client has
    no setting for it and connects with the mcp library's default ("MCP client" in the
    feed). It creates its session with mcp's ClientSession in one place, so we give it a
    ClientSession that already carries the name "Alfred"."""
    pipecat_mcp.ClientSession = partial(ClientSession, client_info=ALFRED)
