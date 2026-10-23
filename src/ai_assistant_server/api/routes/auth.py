from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.core.config import settings
from ai_assistant_server.core.security import create_access_token
from ai_assistant_server.schemas.auth import LoginRequest, RegisterRequest, UserResponse
from ai_assistant_server.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def set_auth_cookie(response: Response, user_id: UUID) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=create_access_token(user_id),
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,  # JavaScript can't read it, so XSS can't steal it
        secure=settings.cookie_secure,  # HTTPS only in production
        samesite="lax",  # not sent on cross-site POSTs (first CSRF defence)
        path="/",
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: DbSession, response: Response):
    try:
        user = await auth_service.register_user(db, data)
    except auth_service.EmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    set_auth_cookie(response, user.id)  # log in right after sign-up
    return user


@router.post("/login", response_model=UserResponse)
async def login(data: LoginRequest, db: DbSession, response: Response):
    user = await auth_service.authenticate_user(db, data.email, data.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User account is inactive")

    set_auth_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return user
