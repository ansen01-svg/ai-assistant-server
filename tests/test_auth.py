from httpx import AsyncClient

from tests.conftest import PASSWORD, MakeClient


async def test_register_login_me(user_client: AsyncClient) -> None:
    r = await user_client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "ansen@example.com"


async def test_wrong_password(make_client: MakeClient) -> None:
    c = await make_client("ansen@example.com")
    r = await c.post("/api/v1/auth/login", json={"email": "ansen@example.com", "password": "wrong"})
    assert r.status_code == 401
    assert PASSWORD != "wrong"


async def test_me_requires_login(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
