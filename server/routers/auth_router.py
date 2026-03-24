from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import User
from auth import verify_password, create_access_token
from database import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀렸습니다")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다")

    token = create_access_token({"sub": user.username, "role": user.role, "group_id": user.group_id})
    return {"access_token": token, "token_type": "bearer", "role": user.role, "username": user.username, "group_id": user.group_id}
