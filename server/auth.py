import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "")
if not SECRET_KEY:
    import secrets as _secrets
    SECRET_KEY = _secrets.token_hex(32)
    import logging
    logging.warning("[보안 경고] JWT_SECRET_KEY 환경변수가 설정되지 않았습니다. 재시작 시 모든 토큰이 무효화됩니다.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    return decode_token(token)


async def _check_active_and_refresh_role(payload: dict, session: AsyncSession) -> dict:
    """DB에서 is_active 확인 및 현재 role 갱신 (강등/비활성화 즉시 반영)."""
    from models import User
    result = await session.execute(select(User).where(User.username == payload.get("sub")))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="비활성화된 계정입니다")
    payload = {**payload, "role": user.role, "group_id": user.group_id}
    return payload


async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(__import__("database", fromlist=["get_session"]).get_session),
) -> dict:
    payload = decode_token(token)
    if payload.get("role") not in ("superadmin", "group_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한 필요")
    payload = await _check_active_and_refresh_role(payload, session)
    if payload["role"] not in ("superadmin", "group_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한 필요")
    return payload


async def get_current_superadmin(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(__import__("database", fromlist=["get_session"]).get_session),
) -> dict:
    payload = decode_token(token)
    if payload.get("role") != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="슈퍼 어드민 권한 필요")
    payload = await _check_active_and_refresh_role(payload, session)
    if payload["role"] != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="슈퍼 어드민 권한 필요")
    return payload
