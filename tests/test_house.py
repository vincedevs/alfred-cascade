"""Wayne Manor: the house is optional, and a missing house never breaks a conversation."""

import asyncio

import pytest

from alfred.config import Config

pytest.importorskip("pipecat.services.mcp_service")
house = pytest.importorskip("alfred.house")


def test_no_house_unless_it_is_set_up():
    tools = asyncio.run(house.wayne_manor_tools(Config(openai_api_key="sk-test")))
    assert tools == []


def test_an_unreachable_house_means_no_house_tools(monkeypatch):
    monkeypatch.setattr(house, "CONNECT_SECONDS", 1)
    config = Config(openai_api_key="sk-test", wayne_manor_url="http://127.0.0.1:9/mcp")
    assert asyncio.run(house.wayne_manor_tools(config)) == []  # nothing listens there


def test_wayne_manor_sees_alfreds_name():
    import anyio

    house.connect_as_alfred()
    send, receive = anyio.create_memory_object_stream(1)
    session = house.pipecat_mcp.ClientSession(receive, send)  # what Pipecat creates
    assert session._client_info.name == "Alfred"
