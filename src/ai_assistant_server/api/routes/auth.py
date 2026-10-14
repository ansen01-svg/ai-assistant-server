from fastapi import APIRouter, HTTPException, status

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.core.security import create_access_token
from ai_assistant_server.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from ai_assistant_server.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: DbSession):
    try:
        return await auth_service.register_user(db, data)
    except auth_service.EmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: DbSession):
    user = await auth_service.authenticate_user(db, data.email, data.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User account is inactive")

    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser):
    return user
