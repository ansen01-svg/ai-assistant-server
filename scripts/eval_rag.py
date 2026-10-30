"""RAG retrieval eval.

Loads evals/rag/docs into a dedicated eval user (fresh each run, so chunking/embedding
changes are measured), runs every question through search_chunks and reports hit@1,
hit@k, MRR and latency. Exits 1 if hit@1 is below --min-hit1.

Usage: uv run python scripts/eval_rag.py [--k 3] [--min-hit1 0.8]
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models import Document, User
from ai_assistant_server.services import documents as document_service

EVAL_DIR = Path(__file__).resolve().parent.parent / "evals" / "rag"
EVAL_EMAIL = "eval@bobby.local"
PROCESS_TIMEOUT_S = 120


async def get_eval_user_id() -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EVAL_EMAIL))
        if user is None:
            user = User(email=EVAL_EMAIL, name="Eval", password_hash=None)
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user.id


async def load_corpus(user_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Document).where(Document.user_id == user_id))
        await db.commit()
        for path in sorted((EVAL_DIR / "docs").glob("*")):
            await document_service.create_document(db, user_id, path.name, path.read_bytes())

    # Process here; a running worker may also pick some up, so wait on status.
    deadline = time.monotonic() + PROCESS_TIMEOUT_S
    while True:
        processed = await document_service.process_next_document()
        async with AsyncSessionLocal() as db:
            statuses = (
                await db.scalars(select(Document.status).where(Document.user_id == user_id))
            ).all()
        if "failed" in statuses:
            raise SystemExit("A corpus document failed to process — check the logs.")
        if all(s == "ready" for s in statuses):
            return
        if time.monotonic() > deadline:
            raise SystemExit("Timed out waiting for corpus processing.")
        if not processed:
            await asyncio.sleep(1)


async def run(k: int, min_hit1: float) -> int:
    user_id = await get_eval_user_id()
    await load_corpus(user_id)
    cases = json.loads((EVAL_DIR / "questions.json").read_text())

    ranks: list[int | None] = []
    latencies: list[float] = []
    async with AsyncSessionLocal() as db:
        for case in cases:
            start = time.perf_counter()
            hits = await document_service.search_chunks(db, user_id, case["question"], top_k=k)
            latencies.append(time.perf_counter() - start)

            rank = next(
                (
                    i + 1
                    for i, h in enumerate(hits)
                    if h.filename == case["file"] and case["expect"].lower() in h.content.lower()
                ),
                None,
            )
            ranks.append(rank)
            mark = f"#{rank}" if rank else "MISS"
            top = f"{hits[0].filename} ({hits[0].score})" if hits else "-"
            print(f"{mark:>5}  {case['question'][:58]:<58}  top: {top}")

    n = len(cases)
    hit1 = sum(r == 1 for r in ranks) / n
    hitk = sum(r is not None for r in ranks) / n
    mrr = sum(1 / r for r in ranks if r) / n
    p50 = statistics.median(latencies) * 1000
    print(
        f"\nhit@1 {hit1:.0%} | hit@{k} {hitk:.0%} | MRR {mrr:.2f} | "
        f"p50 {p50:.0f} ms | {n} questions"
    )
    return 0 if hit1 >= min_hit1 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval eval")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-hit1", type=float, default=0.8)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.k, args.min_hit1)))


if __name__ == "__main__":
    main()
