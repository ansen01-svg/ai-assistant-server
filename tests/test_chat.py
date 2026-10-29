import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

from ai_assistant_server.api.routes import chat as chat_route
from ai_assistant_server.services import chat as chat_service
from ai_assistant_server.services.llm import LLMResponse
from tests.conftest import MakeClient

URL = "/api/v1/chat/stream"


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        event, data = block.split("\n", 1)
        events.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> list[list[Any]]:
    """Replaces Claude. Returns the history the route sent on each request."""
    histories: list[list[Any]] = []

    async def fake_stream_chat(
        history: list[Any], *, model: str, system: str
    ) -> AsyncIterator[str | LLMResponse]:
        histories.append(list(history))
        yield "Hello"
        yield " there"
        yield LLMResponse(
            text="Hello there",
            model=model,
            input_tokens=10,
            output_tokens=5,
            stop_reason="end_turn",
        )

    async def fake_generate_title(user_message: str) -> str:
        return "Mocked Title"

    monkeypatch.setattr(chat_route, "stream_chat", fake_stream_chat)
    monkeypatch.setattr(chat_service, "generate_title", fake_generate_title)
    return histories


async def start_chat(c: AsyncClient, message: str = "Hi") -> str:
    r = await c.post(URL, json={"message": message})
    return parse_sse(r.text)[0][1]["conversation_id"]


async def test_new_conversation(user_client: AsyncClient, fake_llm: list[list[Any]]) -> None:
    r = await user_client.post(URL, json={"message": "Hi"})
    assert r.status_code == 200

    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["start", "delta", "delta", "title", "done"]
    assert "".join(d["text"] for e, d in events if e == "delta") == "Hello there"
    done = events[-1][1]
    assert done["output_tokens"] == 5
    assert done["cost_usd"] is not None

    conversation_id = events[0][1]["conversation_id"]
    r = await user_client.get("/api/v1/conversations")
    assert [c["title"] for c in r.json()] == ["Mocked Title"]

    r = await user_client.get(f"/api/v1/conversations/{conversation_id}")
    messages = [(m["role"], m["content"]) for m in r.json()["messages"]]
    assert messages == [("user", "Hi"), ("assistant", "Hello there")]


async def test_continue_sends_history(user_client: AsyncClient, fake_llm: list[list[Any]]) -> None:
    conversation_id = await start_chat(user_client)

    r = await user_client.post(
        URL, json={"message": "And again", "conversation_id": conversation_id}
    )
    events = parse_sse(r.text)
    assert "title" not in [e for e, _ in events]  # title only for new conversations
    assert [m["content"] for m in fake_llm[-1]] == ["Hi", "Hello there", "And again"]


async def test_conversation_isolation(
    user_client: AsyncClient, make_client: MakeClient, fake_llm: list[list[Any]]
) -> None:
    conversation_id = await start_chat(user_client, "Secret")
    other = await make_client("other@example.com")

    assert (await other.get(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    assert (await other.delete(f"/api/v1/conversations/{conversation_id}")).status_code == 404
    r = await other.post(URL, json={"message": "Hijack", "conversation_id": conversation_id})
    assert r.status_code == 404
    assert (await other.get("/api/v1/conversations")).json() == []


async def test_llm_failure_sends_error_event(
    user_client: AsyncClient, fake_llm: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_stream_chat(
        history: list[Any], *, model: str, system: str
    ) -> AsyncIterator[str]:
        raise RuntimeError("API down")
        yield ""  # makes this an async generator

    monkeypatch.setattr(chat_route, "stream_chat", broken_stream_chat)

    r = await user_client.post(URL, json={"message": "Hi"})
    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["start", "error"]

    # user message is saved, no assistant message
    r = await user_client.get(f"/api/v1/conversations/{events[0][1]['conversation_id']}")
    assert [m["role"] for m in r.json()["messages"]] == ["user"]


async def test_chat_requires_login(client: AsyncClient) -> None:
    r = await client.post(URL, json={"message": "Hi"})
    assert r.status_code == 401
