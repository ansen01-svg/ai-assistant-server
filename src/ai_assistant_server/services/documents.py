import asyncio
import base64
import html
import io
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal, cast

from anthropic.types import ContentBlockParam
from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document, DocumentChunk, Message
from ai_assistant_server.services.embeddings import embed_documents, embed_query, rerank
from ai_assistant_server.services.llm import client as anthropic_client

logger = logging.getLogger(__name__)

ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_EXTENSIONS = set(CONTENT_TYPES) | IMAGE_EXTENSIONS

MAX_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Anthropic per-image limit
CHUNK_CHARS = 3200  # ~800 tokens
OVERLAP_CHARS = 400  # ~100 tokens
STALE_AFTER = timedelta(minutes=10)
INLINE_MAX_CHARS = 50_000  # ~12K tokens: send full text; above this → RAG
MAX_ATTACHMENTS = 3

TRANSCRIBE_MODEL = "claude-haiku-5-5"
TRANSCRIBE_PROMPT = (
    "Transcribe all text in this file exactly, preserving structure as Markdown "
    "(headings, lists, tables). If it is a photo, diagram or whiteboard with little text, "
    "describe its content in detail, including any lists, action items, names, numbers and dates. "
    "Output only the transcription, no commentary."
)


class ExtractionError(Exception):
    pass


class NoTextError(ExtractionError):
    """File was readable but has no text layer (e.g. a scanned PDF)."""


# ---------- extraction, chunking, file blocks (pure) ----------


def sniff_image(data: bytes) -> str | None:
    """Real media type from magic bytes — Claude rejects a wrong media_type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
        raise NoTextError("No text found in file")
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


def file_block(content_type: str, data: bytes) -> ContentBlockParam:
    """Image or PDF as a Claude content block."""
    b64 = base64.standard_b64encode(data).decode()
    if content_type == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    return {
        "type": "image",
        # safe: sniff_image() only stores these four types
        "source": {"type": "base64", "media_type": cast(ImageMediaType, content_type), "data": b64},
    }


# ---------- transcription (images + scanned PDFs) ----------


async def transcribe(content_type: str, data: bytes) -> str:
    resp = await anthropic_client.messages.create(
        model=TRANSCRIBE_MODEL,
        max_tokens=16_000,
        messages=[
            {
                "role": "user",
                "content": [
                    file_block(content_type, data),
                    {"type": "text", "text": TRANSCRIBE_PROMPT},
                ],
            }
        ],
    )
    if resp.stop_reason == "max_tokens":
        logger.warning("Transcription truncated (%s, %d bytes)", content_type, len(data))
    logger.info(
        "Transcribed %s: %d in / %d out tokens",
        content_type,
        resp.usage.input_tokens,
        resp.usage.output_tokens,
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------- chat attachments ----------


@dataclass
class AttachedDoc:
    id: uuid.UUID
    status: str
    text: str
    content_type: str = "text/plain"
    data: bytes | None = None


def render_attachment(filename: str, doc: AttachedDoc | None) -> list[ContentBlockParam]:
    name = html.escape(filename, quote=True)
    if doc is None:
        return [
            {
                "type": "text",
                "text": f'<attachment filename="{name}">This file has been deleted.</attachment>',
            }
        ]

    if doc.data is not None:  # image or scanned PDF: Claude sees the original file
        label = "scanned PDF" if doc.content_type == "application/pdf" else "image"
        return [
            {"type": "text", "text": f'<attachment filename="{name}" type="{label}" />'},
            file_block(doc.content_type, doc.data),
        ]

    if len(doc.text) <= INLINE_MAX_CHARS:
        text = f'<attachment filename="{name}">\n{doc.text}\n</attachment>'
    elif doc.status != "ready":
        text = (
            f'<attachment filename="{name}" id="{doc.id}">'
            "Too long to include and still being indexed. "
            "Tell the user to try again in a moment.</attachment>"
        )
    else:
        text = (
            f'<attachment filename="{name}" id="{doc.id}">Too long to include in full. '
            f"Use search_documents with document_id={doc.id}.</attachment>"
        )
    return [{"type": "text", "text": text}]


async def render_user_messages(
    db: AsyncSession, user_id: uuid.UUID, messages: Sequence[Message]
) -> dict[uuid.UUID, list[ContentBlockParam]]:
    """Message id → content blocks for Claude, with attachments expanded.

    Only messages with attachments.
    """
    ids = {uuid.UUID(a["id"]) for m in messages for a in (m.attachments or [])}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                Document.id,
                Document.status,
                Document.extracted_text,
                Document.content_type,
                Document.file_data,
            ).where(Document.id.in_(ids), Document.user_id == user_id)
        )
    ).all()
    docs = {
        r.id: AttachedDoc(r.id, r.status, r.extracted_text, r.content_type, r.file_data)
        for r in rows
    }

    out: dict[uuid.UUID, list[ContentBlockParam]] = {}
    for m in messages:
        if m.attachments:
            blocks: list[ContentBlockParam] = []
            for a in m.attachments:
                blocks += render_attachment(a["filename"], docs.get(uuid.UUID(a["id"])))
            blocks.append({"type": "text", "text": m.content})
            out[m.id] = blocks
    return out


async def get_owned_documents(
    db: AsyncSession, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[Document]:
    if not ids:
        return []
    result = await db.scalars(
        select(Document).where(Document.id.in_(ids), Document.user_id == user_id)
    )
    return list(result.all())


# ---------- CRUD ----------


async def create_document(
    db: AsyncSession, user_id: uuid.UUID, filename: str, data: bytes
) -> Document:
    """Save the upload as `pending`; the worker transcribes (if needed), chunks and embeds it."""
    ext = Path(filename).suffix.lower()
    base = {"user_id": user_id, "filename": filename[:255], "size_bytes": len(data)}

    if ext in IMAGE_EXTENSIONS:
        media_type = sniff_image(data)
        if media_type is None:
            raise ExtractionError("Not a valid PNG, JPEG, WebP or GIF image")
        doc = Document(
            **base, kind="image", content_type=media_type, extracted_text="", file_data=data
        )
    else:
        try:
            text = await asyncio.to_thread(extract_text, filename, data)
        except NoTextError:
            if ext != ".pdf":
                raise
            # Scanned PDF: keep the file; the worker transcribes it
            doc = Document(
                **base, content_type="application/pdf", extracted_text="", file_data=data
            )
        else:
            doc = Document(**base, content_type=CONTENT_TYPES[ext], extracted_text=text)

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
    """Claim one pending (or stale processing) document, transcribe if needed, chunk + embed.

    Returns False if there was nothing to do.
    """
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
                .returning(
                    Document.id,
                    Document.user_id,
                    Document.extracted_text,
                    Document.content_type,
                    Document.file_data,
                )
            )
        ).one_or_none()
        await db.commit()
    if row is None:
        return False

    doc_id, user_id, text, content_type, data = row
    transcribed = False
    try:
        if not text:  # image or scanned PDF
            if data is None:
                raise ValueError("Document has no text and no file")
            text = await transcribe(content_type, data)
            if not text:
                raise ValueError("Transcription returned no text")
            transcribed = True

        chunks = chunk_text(text)
        embeddings = await embed_documents(chunks)  # network calls outside any DB transaction
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
            values: dict[str, object] = {
                "status": "ready",
                "chunk_count": len(chunks),
                "error": None,
            }
            if transcribed:
                values["extracted_text"] = text  # keep it: re-indexing won't need Claude again
            await db.execute(update(Document).where(Document.id == doc_id).values(**values))
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


# ---------- retrieval ----------

RERANK_CANDIDATES = 20


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
    use_rerank: bool = True,
) -> list[SearchHit]:
    """Vector search for candidates, then rerank them and keep the best top_k."""
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
        .limit(max(RERANK_CANDIDATES, top_k) if use_rerank else top_k)
    )
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == document_id)
    rows = (await db.execute(stmt)).all()

    def hit(i: int, score: float) -> SearchHit:
        r = rows[i]
        return SearchHit(r.document_id, r.filename, r.chunk_index, r.content, round(score, 3))

    if use_rerank and len(rows) > 1:
        try:
            ranked = await rerank(query, [r.content for r in rows], top_k)
            return [hit(i, score) for i, score in ranked]
        except Exception:
            logger.exception("Rerank failed; falling back to vector order")

    return [hit(i, 1 - r.distance) for i, r in enumerate(rows[:top_k])]
