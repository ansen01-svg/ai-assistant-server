import io
import uuid

from pypdf import PdfWriter
from sqlalchemy import select

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document
from ai_assistant_server.models.document import EMBEDDING_DIM
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services.documents import INLINE_MAX_CHARS, AttachedDoc, render_attachment

URL = "/api/v1/documents"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # magic bytes are enough for sniffing


async def fake_transcribe(_content_type: str, _data: bytes) -> str:
    return "Whiteboard: launch on 12 Dec, Ravi owns the budget."


async def fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.1] * EMBEDDING_DIM for _ in texts]


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- render ----------


def test_render_small_inlines_text():
    blocks = render_attachment("a.txt", AttachedDoc(uuid.uuid4(), "pending", "Bobby likes tea."))
    assert "Bobby likes tea." in str(blocks)


def test_render_large_points_to_search():
    doc = AttachedDoc(uuid.uuid4(), "ready", "x" * (INLINE_MAX_CHARS + 1))
    blocks = render_attachment("big.pdf", doc)
    assert f"document_id={doc.id}" in str(blocks)
    assert "xxx" not in str(blocks)


def test_render_deleted():
    assert "deleted" in str(render_attachment("gone.pdf", None))


def test_render_escapes_filename():
    blocks = render_attachment('a"><x.txt', AttachedDoc(uuid.uuid4(), "ready", "hi"))
    assert '"><x' not in str(blocks)


def test_render_image_block():
    doc = AttachedDoc(uuid.uuid4(), "ready", "", "image/png", PNG)
    blocks = render_attachment("board.png", doc)
    assert [b["type"] for b in blocks] == ["text", "image"]


def test_render_image_sends_original_even_after_transcription():
    doc = AttachedDoc(uuid.uuid4(), "ready", "transcribed text", "image/png", PNG)
    blocks = render_attachment("board.png", doc)
    assert [b["type"] for b in blocks] == ["text", "image"]


# ---------- upload + processing ----------


async def test_image_is_transcribed_and_indexed(user_client, monkeypatch):
    monkeypatch.setattr(document_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(document_service, "embed_documents", fake_embed)

    r = await user_client.post(URL, files={"file": ("board.png", PNG, "image/png")})
    assert r.status_code == 201
    assert r.json()["kind"] == "image"
    assert r.json()["status"] == "pending"

    assert await document_service.process_next_document()
    doc = (await user_client.get(f"{URL}/{r.json()['id']}")).json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 1
    assert len((await user_client.get(URL)).json()) == 1  # listed on the Documents page


async def test_scanned_pdf_is_transcribed(user_client, monkeypatch):
    monkeypatch.setattr(document_service, "transcribe", fake_transcribe)
    monkeypatch.setattr(document_service, "embed_documents", fake_embed)

    r = await user_client.post(URL, files={"file": ("scan.pdf", _blank_pdf(), "application/pdf")})
    assert r.status_code == 201
    assert r.json()["status"] == "pending"

    assert await document_service.process_next_document()
    doc = (await user_client.get(f"{URL}/{r.json()['id']}")).json()
    assert doc["status"] == "ready"
    assert doc["chunk_count"] == 1


async def test_empty_transcription_fails(user_client, monkeypatch):
    async def empty(_content_type: str, _data: bytes) -> str:
        return ""

    monkeypatch.setattr(document_service, "transcribe", empty)
    r = await user_client.post(URL, files={"file": ("blank.png", PNG, "image/png")})
    await document_service.process_next_document()
    doc = (await user_client.get(f"{URL}/{r.json()['id']}")).json()
    assert doc["status"] == "failed"


async def test_rejects_fake_image(user_client):
    r = await user_client.post(URL, files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 400


async def test_get_owned_documents_scopes_user(user_client):
    r = await user_client.post(URL, files={"file": ("a.txt", b"hello", "text/plain")})
    doc_id = uuid.UUID(r.json()["id"])
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(select(Document.user_id).where(Document.id == doc_id))
        assert owner is not None
        assert len(await document_service.get_owned_documents(db, owner, [doc_id])) == 1
        assert await document_service.get_owned_documents(db, uuid.uuid4(), [doc_id]) == []
