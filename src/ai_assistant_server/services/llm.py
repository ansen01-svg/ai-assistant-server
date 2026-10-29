import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import anthropic
from anthropic.types import MessageParam, ToolParam
from pydantic import BaseModel, ValidationError

from ai_assistant_server.core.config import settings

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    tool_messages: list[MessageParam] | None = None


MAX_TOOL_ROUNDS = 8


type ToolRunner = Callable[[str, dict[str, Any]], Awaitable[tuple[str, bool]]]


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]

    from collections.abc import AsyncIterator, Awaitable, Callable


async def stream_chat(
    messages: list[MessageParam],
    *,
    model: str,
    system: str,
    tools: list[ToolParam],
    run_tool: ToolRunner,
) -> AsyncIterator[str | ToolCall | LLMResponse]:
    """Yields text chunks and ToolCalls, then one final LLMResponse."""
    new_messages: list[MessageParam] = []
    round_texts: list[str] = []
    input_tokens = output_tokens = 0

    for round_ in range(MAX_TOOL_ROUNDS):
        last_round = round_ == MAX_TOOL_ROUNDS - 1
        separator = "\n\n" if any(round_texts) else ""
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=settings.max_output_tokens,
                system=system,
                messages=[*messages, *new_messages],
                tools=tools,
                # Last round: force a text answer so the loop always ends
                tool_choice={"type": "none"} if last_round else {"type": "auto"},
            ) as stream:
                async for text in stream.text_stream:
                    yield separator + text
                    separator = ""
                final = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(exc.message) from exc

        input_tokens += final.usage.input_tokens
        output_tokens += final.usage.output_tokens
        round_texts.append("".join(b.text for b in final.content if b.type == "text"))
        wants_tools = final.stop_reason == "tool_use"

        content: list[Any] = [
            b.model_dump(mode="json", exclude_none=True)
            for b in final.content
            if wants_tools or b.type != "tool_use"  # drop tool calls cut off by max_tokens
        ]
        new_messages.append({"role": "assistant", "content": content})

        if not wants_tools:
            yield LLMResponse(
                text="\n\n".join(t for t in round_texts if t),
                model=final.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stop_reason=final.stop_reason,
                tool_messages=new_messages if len(new_messages) > 1 else None,
            )
            return

        results: list[Any] = []
        for block in final.content:
            if block.type == "tool_use":
                args = cast(dict[str, Any], block.input)
                yield ToolCall(name=block.name, input=args)
                output, is_error = await run_tool(block.name, args)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                        "is_error": is_error,
                    }
                )
        new_messages.append({"role": "user", "content": results})

    raise LLMError("Tool loop did not finish")


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
    except anthropic.APIError as e:
        logger.error("Structured output failed: %s", e.message)
        raise LLMError("Structured output failed") from e
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
