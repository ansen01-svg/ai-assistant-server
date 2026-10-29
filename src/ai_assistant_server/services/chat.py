import logging
from typing import cast as typing_cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Conversation, Message
from ai_assistant_server.schemas.chat import ChatRequest
from ai_assistant_server.schemas.conversation import ConversationTitle
from ai_assistant_server.services import llm
from ai_assistant_server.services.llm import LLMResponse, MessageParam
from ai_assistant_server.services.pricing import calculate_cost

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Bobby, a helpful assistant.
You can read and change the user's tasks with tools.
- Before updating a task, find it with search_tasks. Never guess IDs.
- If several tasks match, list them and ask which one the user means.
- After a change, confirm briefly what you did."""

HISTORY_LIMIT = 50

TITLE_MODEL = "claude-haiku-5-5"
TITLE_SYSTEM = (
    "You write short, specific titles for chat conversations, "
    "in the language of the user's message."
)


class ConversationNotFoundError(Exception):
    pass


async def prepare_chat(
    db: AsyncSession, user_id: UUID, data: ChatRequest
) -> tuple[Conversation, list[MessageParam]]:
    """Get or create the conversation, save the user message, return the history."""
    if data.conversation_id is None:
        conversation = Conversation(user_id=user_id, title=data.message[:60], model=data.model)
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
    history: list[MessageParam] = []
    for m in reversed(rows.all()):
        if m.tool_messages:
            history.extend(typing_cast(list[MessageParam], m.tool_messages))
        else:
            history.append({"role": m.role, "content": m.content})

    # The API requires the first message to be from the user
    while history and history[0]["role"] != "user":
        history.pop(0)

    return conversation, history


async def save_assistant_message(
    conversation_id: UUID, response: LLMResponse, title: str | None = None
) -> Message:
    # Uses its own session because it runs inside the streaming response
    async with AsyncSessionLocal() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=response.text,
            tool_messages=response.tool_messages,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=calculate_cost(response.model, response.input_tokens, response.output_tokens),
        )
        db.add(message)

        values: dict = {"updated_at": func.now()}
        if title:
            values["title"] = title
        await db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(**values)
        )
        await db.commit()
        return message


async def generate_title(user_message: str) -> str | None:
    """AI title for a new conversation.

    Never raises: returns None so the caller keeps the default title.
    """
    try:
        result = await llm.generate_structured(
            [
                {
                    "role": "user",
                    "content": (
                        "Write a title for a conversation that starts with:\n\n"
                        f"{user_message[:2000]}"
                    ),
                }
            ],
            output_type=ConversationTitle,
            model=TITLE_MODEL,
            system=TITLE_SYSTEM,
        )
    except Exception:
        # The title is optional, so it must never fail the chat
        logger.warning("Title generation failed", exc_info=True)
        return None
    return result.title.strip() or None
