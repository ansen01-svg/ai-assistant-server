from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")  # adjust if your path differs
    assert r.status_code == 200
