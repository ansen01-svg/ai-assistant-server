import asyncio
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None

    conversation_id = conversation.id
    is_new = data.conversation_id is None

    async def event_stream():
        # Generate the title in parallel with the reply
        title_task = (
            asyncio.create_task(chat_service.generate_title(data.message)) if is_new else None
        )
        yield sse("start", {"conversation_id": str(conversation_id)})
        try:
            async for chunk in stream_chat(
                history, model=data.model, system=chat_service.SYSTEM_PROMPT
            ):
                if isinstance(chunk, LLMResponse):
                    title = await title_task if title_task else None
                    message = await chat_service.save_assistant_message(
                        conversation_id,
                        chunk,
                        title=title,
                    )
                    if title:
                        yield sse("title", {"title": title})
                    yield sse(
                        "done",
                        {
                            "message_id": str(message.id),
                            "input_tokens": chunk.input_tokens,
                            "output_tokens": chunk.output_tokens,
                            "cost_usd": float(message.cost_usd)
                            if message.cost_usd is not None
                            else None,
                            "stop_reason": chunk.stop_reason,
                        },
                    )
                else:
                    yield sse("delta", {"text": chunk})
        except Exception:
            # Headers are already sent, so report the error in the stream
            logger.exception("Chat stream failed (conversation=%s)", conversation_id)
            yield sse("error", {"detail": "Something went wrong. Please try again."})
        finally:
            # Don't leave the title task running if the client disconnects
            if title_task:
                title_task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
