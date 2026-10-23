from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Conversation, Message
from ai_assistant_server.schemas.chat import ChatRequest
from ai_assistant_server.services.conversation import ConversationNotFoundError
from ai_assistant_server.services.llm import LLMResponse
from ai_assistant_server.services.pricing import calculate_cost

SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Answer clearly and concisely. "
    "Use Markdown when it improves readability."
)
HISTORY_LIMIT = 50


async def prepare_chat(
    db: AsyncSession, user_id: UUID, data: ChatRequest
) -> tuple[Conversation, list[dict]]:
    """Get or create the conversation, save the user message, return the history."""
    if data.conversation_id is None:
        conversation = Conversation(
            user_id=user_id, title=data.message[:60], model=data.model
        )
        db.add(conversation)
        await db.flush()
    else:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == data.conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if conversation is None:
            raise ConversationNotFoundError

    db.add(Message(conversation_id=conversation.id, role="user", content=data.message))
    await db.commit()

    rows = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(rows.all())]

    # The API requires the first message to be from the user
    while history and history[0]["role"] != "user":
        history.pop(0)

    return conversation, history


async def save_assistant_message(
    conversation_id: UUID, response: LLMResponse
) -> Message:
    # Uses its own session because it runs inside the streaming response
    async with AsyncSessionLocal() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=response.text,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=calculate_cost(
                response.model, response.input_tokens, response.output_tokens
            ),
        )
        db.add(message)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        await db.commit()
        return message
