import asyncio
import html
import io
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document, DocumentChunk, Message
from ai_assistant_server.services.embeddings import embed_documents, embed_query

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
MAX_BYTES = 10 * 1024 * 1024
CHUNK_CHARS = 3200  # ~800 tokens
OVERLAP_CHARS = 400  # ~100 tokens
STALE_AFTER = timedelta(minutes=10)
INLINE_MAX_CHARS = 50_000  # ~12K tokens: send full text; above this → RAG
MAX_ATTACHMENTS = 3


class ExtractionError(Exception):
    pass


# ---------- extraction & chunking (pure, sync) ----------


@dataclass
class AttachedDoc:
    id: uuid.UUID
    status: str
    text: str


def render_attachment(filename: str, doc: AttachedDoc | None) -> str:
    name = html.escape(filename, quote=True)
    if doc is None:
        return f'<attachment filename="{name}">This file has been deleted.</attachment>'
    if len(doc.text) <= INLINE_MAX_CHARS:
        return f'<attachment filename="{name}">\n{doc.text}\n</attachment>'
    if doc.status != "ready":
        note = (
            "Too long to include and still being indexed. Tell the user to try again in a moment."
        )
    else:
        note = f"Too long to include in full. Use search_documents with document_id={doc.id}."
    return f'<attachment filename="{name}" id="{doc.id}">{note}</attachment>'


async def get_owned_documents(
    db: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[Document]:
    if not ids:
        return []
    result = await db.scalars(
        select(Document).where(Document.id.in_(ids), Document.user_id == user_id)
    )
    return list(result.all())


async def render_user_messages(
    db: AsyncSession, user_id: uuid.UUID, messages: Sequence[Message]
) -> dict[uuid.UUID, str]:
    """Message id → content for Claude, with attachments expanded.

    Only messages with attachments.
    """
    ids = {uuid.UUID(a["id"]) for m in messages for a in (m.attachments or [])}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(Document.id, Document.status, Document.extracted_text).where(
                Document.id.in_(ids), Document.user_id == user_id
            )
        )
    ).all()
    docs = {r.id: AttachedDoc(r.id, r.status, r.extracted_text) for r in rows}

    out: dict[uuid.UUID, str] = {}
    for m in messages:
        if m.attachments:
            parts = [
                render_attachment(a["filename"], docs.get(uuid.UUID(a["id"])))
                for a in m.attachments
            ]
            out[m.id] = "\n\n".join(parts) + "\n\n" + m.content
    return out


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            pages = PdfReader(io.BytesIO(data)).pages
            text = "\n\n".join(page.extract_text() or "" for page in pages)
        elif ext == ".docx":
            text = "\n\n".join(p.text for p in DocxDocument(io.BytesIO(data)).paragraphs)
        else:
            text = data.decode("utf-8", errors="replace")
    except Exception as e:
        raise ExtractionError("Could not read file") from e

    text = text.replace("\x00", "").strip()  # Postgres rejects NUL bytes
    if not text:
        raise ExtractionError("No text found (scanned PDFs aren't supported)")
    return text


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            # prefer a paragraph, then sentence, then word boundary in the second half
            for sep in ("\n\n", ". ", "\n", " "):
                cut = text.rfind(sep, start + CHUNK_CHARS // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        if chunk := text[start:end].strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        # overlap: step back, then snap forward to a word start
        back = end - OVERLAP_CHARS
        space = text.find(" ", back, end)
        start = space + 1 if space != -1 else end
    return chunks


# ---------- CRUD ----------


async def create_document(
    db: AsyncSession, user_id: uuid.UUID, filename: str, data: bytes
) -> Document:
    text = await asyncio.to_thread(extract_text, filename, data)
    doc = Document(
        user_id=user_id,
        filename=filename[:255],
        content_type=CONTENT_TYPES[Path(filename).suffix.lower()],
        size_bytes=len(data),
        extracted_text=text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def list_documents(db: AsyncSession, user_id: uuid.UUID) -> Sequence[Document]:
    result = await db.scalars(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(100)
    )
    return result.all()


async def get_document(db: AsyncSession, user_id: uuid.UUID, doc_id: uuid.UUID) -> Document | None:
    return await db.scalar(
        select(Document).where(Document.id == doc_id, Document.user_id == user_id)
    )


async def delete_document(db: AsyncSession, user_id: uuid.UUID, doc_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(Document)
        .where(Document.id == doc_id, Document.user_id == user_id)
        .returning(Document.id)
    )
    deleted = result.scalar_one_or_none()
    await db.commit()
    return deleted is not None  # chunks go via FK CASCADE


# ---------- processing (worker) ----------


async def process_next_document() -> bool:
    """Claim one pending (or stale processing) document, chunk + embed it. Returns False if none."""
    claimable = (
        select(Document.id)
        .where(
            or_(
                Document.status == "pending",
                and_(
                    Document.status == "processing", Document.updated_at < func.now() - STALE_AFTER
                ),
            )
        )
        .order_by(Document.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                update(Document)
                .where(Document.id.in_(claimable))
                .values(status="processing", updated_at=func.now())
                .returning(Document.id, Document.user_id, Document.extracted_text)
            )
        ).one_or_none()
        await db.commit()
    if row is None:
        return False

    doc_id, user_id, text = row
    try:
        chunks = chunk_text(text)
        embeddings = await embed_documents(chunks)  # network call outside any DB transaction
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == doc_id)
            )  # idempotent retry
            db.add_all(
                DocumentChunk(
                    document_id=doc_id, user_id=user_id, chunk_index=i, content=c, embedding=e
                )
                for i, (c, e) in enumerate(zip(chunks, embeddings, strict=True))
            )
            await db.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="ready", chunk_count=len(chunks), error=None)
            )
            await db.commit()
        logger.info("Document %s ready (%d chunks)", doc_id, len(chunks))
    except Exception:
        logger.exception("Document %s processing failed", doc_id)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="failed", error="Processing failed. Try uploading again.")
            )
            await db.commit()
    return True


@dataclass
class SearchHit:
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    content: str
    score: float  # cosine similarity, 1 = identical


async def search_chunks(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    *,
    top_k: int = 5,
    document_id: uuid.UUID | None = None,
) -> list[SearchHit]:
    vector = await embed_query(query)
    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")  # pyright: ignore[reportAttributeAccessIssue]
    stmt = (
        select(
            DocumentChunk.document_id,
            Document.filename,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id, Document.status == "ready")
        .order_by(distance)
        .limit(top_k)
    )
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == document_id)

    rows = (await db.execute(stmt)).all()
    return [
        SearchHit(r.document_id, r.filename, r.chunk_index, r.content, round(1 - r.distance, 3))
        for r in rows
    ]
