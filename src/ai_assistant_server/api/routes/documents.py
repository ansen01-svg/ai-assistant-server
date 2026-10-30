import uuid
from collections.abc import Sequence
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, UploadFile

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.models import Document
from ai_assistant_server.schemas.document import DocumentRead
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services.documents import CONTENT_TYPES, MAX_BYTES, ExtractionError

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=201, response_model=DocumentRead)
async def upload_document(file: UploadFile, user: CurrentUser, db: DbSession) -> Document:
    filename = Path(file.filename or "").name
    if Path(filename).suffix.lower() not in CONTENT_TYPES:
        raise HTTPException(400, "Supported files: PDF, DOCX, TXT, MD")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 10 MB)")
    try:
        return await document_service.create_document(db, user.id, filename, data)
    except ExtractionError as e:
        raise HTTPException(400, str(e)) from e


@router.get("", response_model=list[DocumentRead])
async def list_documents(user: CurrentUser, db: DbSession) -> Sequence[Document]:
    return await document_service.list_documents(db, user.id)


@router.get("/{doc_id}", response_model=DocumentRead)
async def get_document(doc_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Document:
    doc = await document_service.get_document(db, user.id, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    if not await document_service.delete_document(db, user.id, doc_id):
        raise HTTPException(404, "Document not found")
    return Response(status_code=204)
