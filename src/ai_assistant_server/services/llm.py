from collections.abc import AsyncIterator
from dataclasses import dataclass

import anthropic

from ai_assistant_server.core.config import settings

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
    messages: list[dict],
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
