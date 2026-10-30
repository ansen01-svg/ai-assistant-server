from ai_assistant_server.models.document import EMBEDDING_DIM
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services.documents import CHUNK_CHARS, chunk_text

URL = "/api/v1/documents"


async def fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.1] * EMBEDDING_DIM for _ in texts]


def test_chunk_text_sizes_and_overlap():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(30))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert chunks[0][-50:].split()[-1] in chunks[1]  # overlap


def test_chunk_text_short():
    assert chunk_text("hello") == ["hello"]


async def test_upload_and_process(user_client, monkeypatch):
    monkeypatch.setattr(document_service, "embed_documents", fake_embed)
    r = await user_client.post(
        URL, files={"file": ("notes.txt", b"Bobby likes tea. " * 600, "text/plain")}
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["status"] == "pending"

    assert await document_service.process_next_document()
    assert not await document_service.process_next_document()

    doc = (await user_client.get(f"{URL}/{doc['id']}")).json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] > 1


async def test_embedding_failure_marks_failed(user_client, monkeypatch):
    async def boom(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("down")

    monkeypatch.setattr(document_service, "embed_documents", boom)
    r = await user_client.post(URL, files={"file": ("a.md", b"# Hi\ntext", "text/markdown")})
    await document_service.process_next_document()
    doc = (await user_client.get(f"{URL}/{r.json()['id']}")).json()
    assert doc["status"] == "failed"


async def test_rejects_unsupported_and_empty(user_client):
    r = await user_client.post(URL, files={"file": ("a.exe", b"x", "application/octet-stream")})
    assert r.status_code == 400
    r = await user_client.post(URL, files={"file": ("a.txt", b"   ", "text/plain")})
    assert r.status_code == 400


async def test_delete(user_client):
    r = await user_client.post(URL, files={"file": ("a.txt", b"hello", "text/plain")})
    doc_id = r.json()["id"]
    assert (await user_client.delete(f"{URL}/{doc_id}")).status_code == 204
    assert (await user_client.get(f"{URL}/{doc_id}")).status_code == 404
