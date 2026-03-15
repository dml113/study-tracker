from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from models import User, ActivityLog, Attendance, Absence
from auth import hash_password, get_current_admin
from database import get_session
from datetime import date

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "member"


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    result = await session.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()

    today = date.today().isoformat()
    stats_result = await session.execute(
        select(ActivityLog.username, ActivityLog.active_seconds).where(ActivityLog.date == today)
    )
    today_stats = {row.username: row.active_seconds for row in stats_result}

    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
            "today_seconds": today_stats.get(u.username, 0),
            "today_minutes": round(today_stats.get(u.username, 0) / 60, 1),
        }
        for u in users
    ]


@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    existing = await session.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자명입니다")

    user = User(username=req.username, password_hash=hash_password(req.password), role=req.role)
    session.add(user)
    await session.commit()
    return {"message": f"'{req.username}' 계정이 생성되었습니다"}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")

    if req.password is not None:
        user.password_hash = hash_password(req.password)
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.role is not None:
        user.role = req.role

    await session.commit()
    return {"message": "수정되었습니다"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if user.username == current["sub"]:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다")

    await session.delete(user)
    await session.commit()
    return {"message": "삭제되었습니다"}


@router.get("/attendance")
async def get_attendance(
    target_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    today = target_date or date.today().isoformat()

    # 전체 유저
    users_result = await session.execute(select(User).order_by(User.username))
    users = users_result.scalars().all()

    # 출퇴근
    att_result = await session.execute(
        select(Attendance).where(Attendance.date == today)
    )
    attendances = {a.username: a for a in att_result.scalars().all()}

    # 활동 시간
    stats_result = await session.execute(
        select(ActivityLog.username, ActivityLog.active_seconds).where(ActivityLog.date == today)
    )
    stats = {row.username: row.active_seconds for row in stats_result}

    # 외출 기록
    absence_result = await session.execute(
        select(Absence).where(Absence.date == today).order_by(Absence.start_at)
    )
    absences_by_user: dict[str, list] = {}
    for ab in absence_result.scalars().all():
        absences_by_user.setdefault(ab.username, []).append({
            "start": ab.start_at.strftime("%H:%M"),
            "end": ab.end_at.strftime("%H:%M") if ab.end_at else None,
            "reason": ab.reason,
        })

    return [
        {
            "username": u.username,
            "checkin_at": attendances[u.username].checkin_at.strftime("%H:%M")
            if u.username in attendances and attendances[u.username].checkin_at else None,
            "checkout_at": attendances[u.username].checkout_at.strftime("%H:%M")
            if u.username in attendances and attendances[u.username].checkout_at else None,
            "active_minutes": round(stats.get(u.username, 0) / 60, 1),
            "absences": absences_by_user.get(u.username, []),
        }
        for u in users
    ]
