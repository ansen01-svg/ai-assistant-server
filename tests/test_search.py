import uuid

from sqlalchemy import select

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document
from ai_assistant_server.models.document import EMBEDDING_DIM
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services.tools import run_tool

URL = "/api/v1/documents"


def _vec(text: str) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[0 if "tea" in text.lower() else 1] = 1.0
    return v


async def fake_embed_documents(texts: list[str]) -> list[list[float]]:
    return [_vec(t) for t in texts]


async def fake_embed_query(text: str) -> list[float]:
    return _vec(text)


async def _upload(client, name: str, body: bytes) -> None:
    r = await client.post(URL, files={"file": (name, body, "text/plain")})
    assert r.status_code == 201
    assert await document_service.process_next_document()


async def _user_id() -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        user_id = await db.scalar(select(Document.user_id).limit(1))
    assert user_id is not None
    return user_id


async def test_search_ranks_and_scopes(user_client, monkeypatch):
    monkeypatch.setattr(document_service, "embed_documents", fake_embed_documents)
    monkeypatch.setattr(document_service, "embed_query", fake_embed_query)
    await _upload(user_client, "tea.txt", b"Bobby drinks green tea every morning.")
    await _upload(user_client, "coffee.txt", b"Espresso is brewed under pressure.")
    user_id = await _user_id()

    async with AsyncSessionLocal() as db:
        hits = await document_service.search_chunks(db, user_id, "tea", top_k=1)
        assert hits[0].filename == "tea.txt"
        assert hits[0].score == 1.0
        # another user sees nothing
        assert await document_service.search_chunks(db, uuid.uuid4(), "tea") == []


async def test_search_documents_tool(user_client, monkeypatch):
    monkeypatch.setattr(document_service, "embed_documents", fake_embed_documents)
    monkeypatch.setattr(document_service, "embed_query", fake_embed_query)
    await _upload(user_client, "tea.txt", b"Bobby drinks green tea.")

    output, is_error = await run_tool(await _user_id(), "search_documents", {"query": "tea"})
    assert not is_error
    assert "tea.txt, chunk 1" in str(output)
