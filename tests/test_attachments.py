import uuid

from sqlalchemy import select

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services.documents import INLINE_MAX_CHARS, AttachedDoc, render_attachment


def test_render_small_inlines_text():
    out = render_attachment("a.txt", AttachedDoc(uuid.uuid4(), "pending", "Bobby likes tea."))
    assert "Bobby likes tea." in out


def test_render_large_points_to_search():
    doc = AttachedDoc(uuid.uuid4(), "ready", "x" * (INLINE_MAX_CHARS + 1))
    out = render_attachment("big.pdf", doc)
    assert f"document_id={doc.id}" in out
    assert "xxx" not in out


def test_render_deleted():
    assert "deleted" in render_attachment("gone.pdf", None)


def test_render_escapes_filename():
    out = render_attachment('a"><x.txt', AttachedDoc(uuid.uuid4(), "ready", "hi"))
    assert '"><x' not in out


async def test_get_owned_documents_scopes_user(user_client):
    r = await user_client.post(
        "/api/v1/documents", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    doc_id = uuid.UUID(r.json()["id"])
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(select(Document.user_id).where(Document.id == doc_id))
        assert owner is not None
        assert len(await document_service.get_owned_documents(db, owner, [doc_id])) == 1
        assert await document_service.get_owned_documents(db, uuid.uuid4(), [doc_id]) == []
