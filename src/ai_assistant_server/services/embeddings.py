import asyncio
import logging
from typing import Any

import httpx

from ai_assistant_server.core.config import settings

logger = logging.getLogger(__name__)

EMBED_MODEL = "voyage-4-lite"
RERANK_MODEL = "rerank-3-lite"
BASE_URL = "https://api.voyageai.com/v1"
BATCH_SIZE = 128
MAX_RETRIES = 5

_client = httpx.AsyncClient(timeout=60)


class EmbeddingError(Exception):
    pass


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to Voyage with retries on 429/5xx (honors Retry-After)."""
    if not settings.voyage_api_key:
        raise EmbeddingError("VOYAGE_API_KEY is not set")
    for attempt in range(MAX_RETRIES):
        resp = await _client.post(
            f"{BASE_URL}/{path}",
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json=payload,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
            retry_after = resp.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else min(2 ** (attempt + 1), 30)
            logger.warning("Voyage %s %s, retrying in %.0fs", path, resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        logger.error("Voyage %s error %s: %s", path, resp.status_code, resp.text)
        raise EmbeddingError(f"Voyage {path} failed ({resp.status_code})")
    raise EmbeddingError(f"Voyage {path} failed")


async def _embed_batch(texts: list[str], input_type: str) -> list[list[float]]:
    data = await _post(
        "embeddings", {"input": texts, "model": EMBED_MODEL, "input_type": input_type}
    )
    return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        out += await _embed_batch(texts[i : i + BATCH_SIZE], "document")
    return out


async def embed_query(text: str) -> list[float]:
    return (await _embed_batch([text], "query"))[0]


async def rerank(query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
    """Return (index into documents, relevance score), best first."""
    if not documents:
        return []
    data = await _post(
        "rerank",
        {
            "query": query,
            "documents": documents,
            "model": RERANK_MODEL,
            "top_k": top_k,
            "return_documents": False,
        },
    )
    return [(d["index"], d["relevance_score"]) for d in data["data"]]
