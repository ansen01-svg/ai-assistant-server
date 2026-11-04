import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import anthropic
from anthropic.types import ContentBlock, MessageParam, ToolUnionParam
from pydantic import BaseModel, ValidationError

from ai_assistant_server.core.config import settings

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())

MAX_TOOL_ROUNDS = 8
CONTINUE_REASONS = ("tool_use", "pause_turn")  # the turn isn't finished yet


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
    web_searches: int = 0


type ToolRunner = Callable[[str, dict[str, Any]], Awaitable[tuple[str, bool]]]


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]


def _replayable(blocks: Sequence[ContentBlock], continues: bool) -> list[Any]:
    """Assistant content safe to send back. If the turn ended (e.g. max_tokens), drop tool
    calls that never got a result — replaying them makes the API reject the conversation."""
    answered = {getattr(b, "tool_use_id", None) for b in blocks}

    def keep(b: ContentBlock) -> bool:
        if continues:
            return True
        if b.type == "tool_use":
            return False
        if b.type == "server_tool_use":
            return b.id in answered
        return True

    return [b.model_dump(mode="json", exclude_none=True) for b in blocks if keep(b)]


def _sources_footer(sources: dict[str, str]) -> str:
    if not sources:
        return ""
    lines = [
        f"- [{title.replace('[', '(').replace(']', ')')}]({url})" for url, title in sources.items()
    ]
    return "\n\n**Sources**\n" + "\n".join(lines)


async def stream_chat(
    messages: list[MessageParam],
    *,
    model: str,
    system: str,
    tools: list[ToolUnionParam],
    run_tool: ToolRunner,
) -> AsyncIterator[str | ToolCall | LLMResponse]:
    """Yields text chunks and ToolCalls, then one final LLMResponse."""
    new_messages: list[MessageParam] = []
    round_texts: list[str] = []
    sources: dict[str, str] = {}  # url → title, from web search citations
    input_tokens = output_tokens = web_searches = 0

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
                async for event in stream:
                    if event.type == "text":
                        yield separator + event.text
                        separator = ""
                    elif (
                        event.type == "content_block_start"
                        and event.content_block.type == "server_tool_use"
                    ):
                        # Anthropic runs it; show the label right away
                        yield ToolCall(name=event.content_block.name, input={})
                final = await stream.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(exc.message) from exc

        input_tokens += final.usage.input_tokens
        output_tokens += final.usage.output_tokens
        if final.usage.server_tool_use:
            web_searches += final.usage.server_tool_use.web_search_requests or 0

        for b in final.content:
            if b.type == "text":
                for c in b.citations or []:
                    if c.type == "web_search_result_location":
                        sources.setdefault(c.url, c.title or c.url)
        round_texts.append("".join(b.text for b in final.content if b.type == "text"))

        continues = final.stop_reason in CONTINUE_REASONS
        new_messages.append({"role": "assistant", "content": _replayable(final.content, continues)})

        if final.stop_reason == "pause_turn":
            continue  # long server-side search: re-send as-is so Claude resumes

        if not continues:
            footer = _sources_footer(sources)
            if footer:
                yield footer
            yield LLMResponse(
                text="\n\n".join(t for t in round_texts if t) + footer,
                model=final.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stop_reason=final.stop_reason,
                tool_messages=new_messages if len(new_messages) > 1 else None,
                web_searches=web_searches,
            )
            return

        # stop_reason == "tool_use": run our tools (server tools already ran or run next request)
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
