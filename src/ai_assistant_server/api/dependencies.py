from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.core.config import settings
from ai_assistant_server.core.security import decode_access_token
from ai_assistant_server.db.database import get_db
from ai_assistant_server.models.user import User

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

DbSession = Annotated[AsyncSession, Depends(get_db)]


def verify_origin(request: Request) -> None:
    """CSRF protection for cookie auth.

    Browsers always send an Origin header on cross-site writes, so a write from an
    origin we don't trust is rejected. Same-origin requests (Swagger at /docs) are allowed.
    Requests without Origin come from non-browser clients (curl), which can't carry
    a victim's cookie, so they're safe to allow.
    """
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    if origin in settings.cors_origins or urlparse(origin).netloc == request.headers.get("host"):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Origin not allowed")


async def get_current_user(request: Request, db: DbSession) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
    token = request.cookies.get(settings.auth_cookie_name)
    if token is None:
        raise credentials_error

    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_error

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
