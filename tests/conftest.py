import os

# Point the whole app at the test DB before anything imports settings.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/ai_assistant_test",
)

from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from ai_assistant_server import models  # noqa: E402, F401  (registers all tables)
from ai_assistant_server.db.base import Base  # noqa: E402
from ai_assistant_server.db.database import engine  # noqa: E402
from ai_assistant_server.main import app  # noqa: E402

ORIGIN = "http://localhost:3000"
PASSWORD = "correct-horse-battery"

MakeClient = Callable[..., Awaitable[AsyncClient]]


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    assert engine.url.database == "ai_assistant_test", "Refusing to run tests on a non-test DB"
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
async def make_client() -> AsyncIterator[MakeClient]:
    """Each client has its own cookie jar, so each one acts as a separate logged-in user."""
    clients: list[AsyncClient] = []

    async def _make(email: str | None = None) -> AsyncClient:
        c = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": ORIGIN}
        )
        clients.append(c)
        if email:
            r = await c.post(
                "/api/v1/auth/register",
                json={"email": email, "password": PASSWORD, "name": "Test User"},
            )
            assert r.is_success, r.text
            r = await c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert r.is_success, r.text
        return c

    yield _make
    for c in clients:
        await c.aclose()


@pytest.fixture
async def client(make_client: MakeClient) -> AsyncClient:
    return await make_client()


@pytest.fixture
async def user_client(make_client: MakeClient) -> AsyncClient:
    return await make_client("ansen@example.com")
