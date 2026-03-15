from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from models import ActivityLog, Attendance, Absence
from auth import get_current_user
from database import get_session
from datetime import date, datetime

router = APIRouter(prefix="/api", tags=["api"])


class HeartbeatRequest(BaseModel):
    active_seconds: float


class AbsenceStartRequest(BaseModel):
    reason: str


@router.get("/attendance/today")
async def today_attendance(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()

    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()

    absence_result = await session.execute(
        select(Absence).where(
            Absence.username == username,
            Absence.date == today,
            Absence.end_at == None,
        )
    )
    active_absence = absence_result.scalar_one_or_none()

    return {
        "checked_in": att is not None and att.checkin_at is not None and att.checkout_at is None,
        "checkin_at": att.checkin_at.strftime("%H:%M") if att and att.checkin_at else None,
        "checkout_at": att.checkout_at.strftime("%H:%M") if att and att.checkout_at else None,
        "is_absent": active_absence is not None,
        "absence_reason": active_absence.reason if active_absence else None,
    }


@router.post("/checkin")
async def checkin(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    now = datetime.now()

    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()

    if att and att.checkin_at and not att.checkout_at:
        raise HTTPException(status_code=400, detail="이미 출근 중입니다")

    if not att:
        att = Attendance(username=username, date=today, checkin_at=now)
        session.add(att)
    else:
        att.checkin_at = now
        att.checkout_at = None

    await session.commit()
    return {"status": "ok", "checkin_at": now.strftime("%H:%M")}


@router.post("/checkout")
async def checkout(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    now = datetime.now()

    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()

    if not att or not att.checkin_at:
        raise HTTPException(status_code=400, detail="출근 기록이 없습니다")
    if att.checkout_at:
        raise HTTPException(status_code=400, detail="이미 퇴근했습니다")

    # 미종료 외출 자동 종료
    absence_result = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == today, Absence.end_at == None
        )
    )
    active_absence = absence_result.scalar_one_or_none()
    if active_absence:
        active_absence.end_at = now

    att.checkout_at = now
    await session.commit()
    return {"status": "ok", "checkout_at": now.strftime("%H:%M")}


@router.post("/absence/start")
async def start_absence(
    req: AbsenceStartRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    now = datetime.now()

    # 출근 여부 확인
    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()
    if not att or not att.checkin_at or att.checkout_at:
        raise HTTPException(status_code=400, detail="출근 중이 아닙니다")

    # 이미 외출 중인지 확인
    existing = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == today, Absence.end_at == None
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 외출 중입니다")

    absence = Absence(username=username, date=today, start_at=now, reason=req.reason)
    session.add(absence)
    await session.commit()
    return {"status": "ok", "start_at": now.strftime("%H:%M")}


@router.post("/absence/end")
async def end_absence(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    now = datetime.now()

    result = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == today, Absence.end_at == None
        )
    )
    absence = result.scalar_one_or_none()
    if not absence:
        raise HTTPException(status_code=400, detail="외출 기록이 없습니다")

    absence.end_at = now
    await session.commit()
    return {"status": "ok", "end_at": now.strftime("%H:%M")}


@router.post("/heartbeat")
async def heartbeat(
    req: HeartbeatRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    now = datetime.now()

    # 출근 중인지 확인
    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()
    if not att or not att.checkin_at or att.checkout_at:
        raise HTTPException(status_code=400, detail="출근 중이 아닙니다")

    result = await session.execute(
        select(ActivityLog).where(ActivityLog.username == username, ActivityLog.date == today)
    )
    log = result.scalar_one_or_none()

    if log:
        log.active_seconds += req.active_seconds
        log.last_updated = now
    else:
        log = ActivityLog(username=username, date=today, active_seconds=req.active_seconds)
        session.add(log)

    await session.commit()
    return {"status": "ok"}


@router.get("/stats")
async def get_stats(
    target_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    today = target_date or date.today().isoformat()
    result = await session.execute(
        select(ActivityLog)
        .where(ActivityLog.date == today)
        .order_by(ActivityLog.active_seconds.desc())
    )
    logs = result.scalars().all()
    return [
        {
            "username": log.username,
            "date": log.date,
            "active_seconds": log.active_seconds,
            "active_minutes": round(log.active_seconds / 60, 1),
            "last_updated": log.last_updated.isoformat(),
        }
        for log in logs
    ]
