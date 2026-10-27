import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anthropic
from anthropic.types import MessageParam
from pydantic import BaseModel, ValidationError

from ai_assistant_server.core.config import settings

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None


async def stream_chat(
    messages: list[MessageParam],
    *,
    model: str,
    system: str,
) -> AsyncIterator[str | LLMResponse]:
    """Yields text chunks, then one final LLMResponse."""
    try:
        async with client.messages.stream(
            model=model,
            max_tokens=settings.max_output_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
    except anthropic.APIError as exc:
        raise LLMError(str(exc)) from exc

    yield LLMResponse(
        text="".join(block.text for block in final.content if block.type == "text"),
        model=final.model,
        input_tokens=final.usage.input_tokens,
        output_tokens=final.usage.output_tokens,
        stop_reason=final.stop_reason,
    )


async def generate_structured[T: BaseModel](
    messages: list[MessageParam],
    *,
    output_type: type[T],
    model: str,
    system: str,
    max_tokens: int = 1024,
) -> T:
    """Returns Claude's reply parsed and validated as `output_type`."""
    try:
        response = await client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_format=output_type,
        )
    except anthropic.APIError as exc:
        raise LLMError(str(exc)) from exc
    except ValidationError as exc:
        raise LLMError(f"Invalid structured output: {exc}") from exc

    if response.parsed_output is None:
        raise LLMError(f"No structured output (stop_reason={response.stop_reason})")

    logger.info(
        "Structured output %s: %d in / %d out tokens",
        output_type.__name__,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return response.parsed_output
