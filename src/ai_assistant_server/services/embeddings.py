import asyncio
import logging

import httpx

from ai_assistant_server.core.config import settings

logger = logging.getLogger(__name__)

MODEL = "voyage-4-lite"
URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 128
MAX_RETRIES = 5

_client = httpx.AsyncClient(timeout=60)


class EmbeddingError(Exception):
    pass


async def _embed_batch(texts: list[str], input_type: str) -> list[list[float]]:
    if not settings.voyage_api_key:
        raise EmbeddingError("VOYAGE_API_KEY is not set")
    for attempt in range(MAX_RETRIES):
        resp = await _client.post(
            URL,
            headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
            json={"input": texts, "model": MODEL, "input_type": input_type},
        )
        if resp.status_code == 200:
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        if resp.status_code in (429, 500, 502, 503) and attempt < MAX_RETRIES - 1:
            retry_after = resp.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else min(2 ** (attempt + 1), 30)
            logger.warning("Voyage %s, retrying in %.0fs", resp.status_code, delay)
            await asyncio.sleep(delay)
            continue
        logger.error("Voyage error %s: %s", resp.status_code, resp.text)
        raise EmbeddingError(f"Embedding failed ({resp.status_code})")
    raise EmbeddingError("Embedding failed")


async def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        out += await _embed_batch(texts[i : i + BATCH_SIZE], "document")
    return out


async def embed_query(text: str) -> list[float]:
    return (await _embed_batch([text], "query"))[0]
