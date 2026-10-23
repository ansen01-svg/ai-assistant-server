import json
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.schemas.chat import ChatRequest
from ai_assistant_server.services import chat as chat_service
from ai_assistant_server.services.llm import LLMResponse, stream_chat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/stream")
async def chat_stream(data: ChatRequest, user: CurrentUser, db: DbSession):
    try:
        conversation, history = await chat_service.prepare_chat(db, user.id, data)
    except chat_service.ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    conversation_id = conversation.id

    async def event_stream():
        yield sse("start", {"conversation_id": str(conversation_id)})
        try:
            async for chunk in stream_chat(
                history, model=data.model, system=chat_service.SYSTEM_PROMPT
            ):
                if isinstance(chunk, LLMResponse):
                    message = await chat_service.save_assistant_message(conversation_id, chunk)
                    yield sse("done", {
                        "message_id": str(message.id),
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                        "stop_reason": chunk.stop_reason,
                    })
                else:
                    yield sse("delta", {"text": chunk})
        except Exception:
            # Headers are already sent, so report the error in the stream
            logger.exception("Chat stream failed (conversation=%s)", conversation_id)
            yield sse("error", {"detail": "Something went wrong. Please try again."})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
