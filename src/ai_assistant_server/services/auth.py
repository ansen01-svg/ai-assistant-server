from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.core.security import hash_password, verify_password
from ai_assistant_server.models.user import User
from ai_assistant_server.schemas.auth import RegisterRequest


class EmailAlreadyRegisteredError(Exception):
    pass


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def register_user(db: AsyncSession, data: RegisterRequest) -> User:
    user = User(
        email=data.email,
        name=data.name,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise EmailAlreadyRegisteredError from exc
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    user = await get_user_by_email(db, email)
    if not verify_password(password, user.password_hash if user else None):
        return None
    return user
