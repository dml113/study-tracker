from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from models import ActivityLog, Attendance, Absence, CheatLog, User, StudyGoal
from auth import get_current_user, verify_password, hash_password
from database import get_session
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/api", tags=["api"])


class HeartbeatRequest(BaseModel):
    active_seconds: float


class CheatReportRequest(BaseModel):
    reason: str


class AbsenceStartRequest(BaseModel):
    reason: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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


@router.get("/attendance/live")
async def live_attendance(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    today = date.today().isoformat()

    att_result = await session.execute(
        select(Attendance).where(
            Attendance.date == today,
            Attendance.checkin_at != None,
            Attendance.checkout_at == None,
        )
    )
    attendances = att_result.scalars().all()

    absence_result = await session.execute(
        select(Absence).where(Absence.date == today, Absence.end_at == None)
    )
    active_absences = {a.username: a.reason for a in absence_result.scalars().all()}

    return [
        {
            "username": a.username,
            "checkin_at": a.checkin_at.strftime("%H:%M"),
            "is_absent": a.username in active_absences,
            "absence_reason": active_absences.get(a.username),
        }
        for a in sorted(attendances, key=lambda x: x.checkin_at)
    ]


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

    att_result = await session.execute(
        select(Attendance).where(Attendance.username == username, Attendance.date == today)
    )
    att = att_result.scalar_one_or_none()
    if not att or not att.checkin_at or att.checkout_at:
        raise HTTPException(status_code=400, detail="출근 중이 아닙니다")

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


@router.post("/cheat-report")
async def cheat_report(
    req: CheatReportRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    today = date.today().isoformat()
    log = CheatLog(username=username, date=today, reason=req.reason)
    session.add(log)
    await session.commit()
    return {"status": "ok"}


@router.post("/change-password")
async def change_password_endpoint(
    req: ChangePasswordRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")
    user.password_hash = hash_password(req.new_password)
    await session.commit()
    return {"message": "비밀번호가 변경되었습니다"}


@router.get("/my-stats")
async def my_stats(
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    end_d = date.today()
    start_d = end_d - timedelta(days=days - 1)

    logs_result = await session.execute(
        select(ActivityLog)
        .where(ActivityLog.username == username,
               ActivityLog.date.between(start_d.isoformat(), end_d.isoformat()))
    )
    logs = {log.date: log.active_seconds for log in logs_result.scalars().all()}

    att_result = await session.execute(
        select(Attendance).where(
            Attendance.username == username,
            Attendance.date.between(start_d.isoformat(), end_d.isoformat()),
            Attendance.checkin_at != None,
        )
    )
    attended_dates = {att.date for att in att_result.scalars().all()}

    daily = []
    temp_streak = 0
    max_streak = 0
    for i in range(days):
        d = (start_d + timedelta(days=i)).isoformat()
        secs = logs.get(d, 0)
        daily.append({"date": d, "active_seconds": secs,
                      "active_minutes": round(secs / 60, 1), "attended": d in attended_dates})
        if secs > 0:
            temp_streak += 1
            max_streak = max(max_streak, temp_streak)
        else:
            temp_streak = 0

    current_streak = 0
    for item in reversed(daily):
        if item["active_seconds"] > 0:
            current_streak += 1
        else:
            break

    weeks = []
    for w in range(4):
        wend = end_d - timedelta(days=w * 7)
        wstart = wend - timedelta(days=6)
        wsecs = sum(logs.get((wstart + timedelta(days=i)).isoformat(), 0) for i in range(7))
        weeks.append({"start": wstart.isoformat(), "end": wend.isoformat(),
                      "active_minutes": round(wsecs / 60, 1)})

    # 목표 조회
    user_row = (await session.execute(select(User.group_id).where(User.username == username))).first()
    group_id = user_row[0] if user_row else None
    goals = (await session.execute(select(StudyGoal))).scalars().all()
    goal_by_group = {g.group_id: g.daily_target_minutes for g in goals}
    daily_goal = goal_by_group.get(group_id, goal_by_group.get(None, 480))

    return {
        "username": username,
        "period": {"start": start_d.isoformat(), "end": end_d.isoformat(), "days": days},
        "total_minutes": round(sum(logs.values()) / 60, 1),
        "attend_days": len(attended_dates),
        "current_streak": current_streak,
        "max_streak": max_streak,
        "daily_goal_minutes": daily_goal,
        "daily": daily,
        "weekly": weeks,
    }


@router.get("/stats")
async def get_stats(
    target_date: Optional[str] = None,
    period: str = "daily",
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    d = target_date or date.today().isoformat()
    d_obj = date.fromisoformat(d)

    if period == "weekly":
        start = d_obj - timedelta(days=d_obj.weekday())
        end = start + timedelta(days=6)
        period_days = 7
    elif period == "monthly":
        start = d_obj.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        period_days = (end - start).days + 1
    else:
        start = end = d_obj
        period_days = 1

    rows = (await session.execute(
        select(ActivityLog.username, func.sum(ActivityLog.active_seconds).label("total"))
        .where(ActivityLog.date.between(start.isoformat(), end.isoformat()))
        .group_by(ActivityLog.username)
        .order_by(func.sum(ActivityLog.active_seconds).desc())
    )).all()

    user_groups = {
        row.username: row.group_id
        for row in (await session.execute(select(User.username, User.group_id))).all()
    }
    goals = (await session.execute(select(StudyGoal))).scalars().all()
    goal_by_group = {g.group_id: g.daily_target_minutes for g in goals}
    default_goal = goal_by_group.get(None, 480)

    # 알 부화 계산용 평생 누적 공부시간
    lifetime_rows = (await session.execute(
        select(ActivityLog.username, func.sum(ActivityLog.active_seconds).label("total"))
        .group_by(ActivityLog.username)
    )).all()
    lifetime_map = {row.username: row.total or 0 for row in lifetime_rows}

    result = []
    for row in rows:
        if row.username not in user_groups:
            continue
        group_id = user_groups.get(row.username)
        daily_goal = goal_by_group.get(group_id, default_goal)
        period_goal = daily_goal * period_days
        active_minutes = round(row.total / 60, 1)
        lifetime_minutes = round(lifetime_map.get(row.username, 0) / 60, 1)
        result.append({
            "username": row.username,
            "active_seconds": row.total,
            "active_minutes": active_minutes,
            "goal_minutes": period_goal,
            "achievement_rate": round(active_minutes / period_goal * 100, 1) if period_goal > 0 else 0,
            "period": period,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "lifetime_minutes": lifetime_minutes,
        })
    return result
