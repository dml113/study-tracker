from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import User
from auth import verify_password, create_access_token
from database import get_session
import time
import threading

router = APIRouter(prefix="/auth", tags=["auth"])

# 간단한 in-memory 로그인 레이트 리미터
# IP당 최대 10회/분, 실패 시 잠금
_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()
_RATE_WINDOW = 60   # 초
_RATE_MAX = 10      # 윈도우 내 최대 시도 횟수


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    with _login_lock:
        attempts = _login_attempts.get(ip, [])
        # 윈도우 밖의 기록 제거
        attempts = [t for t in attempts if now - t < _RATE_WINDOW]
        if len(attempts) >= _RATE_MAX:
            raise HTTPException(
                status_code=429,
                detail="로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                headers={"Retry-After": str(_RATE_WINDOW)},
            )
        attempts.append(now)
        _login_attempts[ip] = attempts


@router.post("/login")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    result = await session.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀렸습니다")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다")

    token = create_access_token({"sub": user.username, "role": user.role, "group_id": user.group_id})
    return {"access_token": token, "token_type": "bearer", "role": user.role, "username": user.username, "group_id": user.group_id}
