from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Literal, Optional
from models import ActivityLog, Attendance, Absence, CheatLog, User, StudyGoal, Feedback, Notice, Group
from auth import get_current_user, verify_password, hash_password
from database import get_session
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/api", tags=["api"])


async def get_active_attendance(session: AsyncSession, username: str):
    """현재 출근 중인 Attendance 반환. 자정 넘은 경우 전날 기록도 fallback (04:00 이전까지)."""
    now = datetime.now()
    today = date.today().isoformat()
    result = await session.execute(
        select(Attendance).where(
            Attendance.username == username,
            Attendance.date == today,
            Attendance.checkin_at != None,
            Attendance.checkout_at == None,
        )
    )
    att = result.scalar_one_or_none()
    if att:
        return att, today
    if now.hour < 4:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = await session.execute(
            select(Attendance).where(
                Attendance.username == username,
                Attendance.date == yesterday,
                Attendance.checkin_at != None,
                Attendance.checkout_at == None,
            )
        )
        att = result.scalar_one_or_none()
        if att:
            return att, yesterday
    return None, today


class HeartbeatRequest(BaseModel):
    active_seconds: float = Field(..., gt=0, le=60)
    client_version: Optional[str] = Field(None, max_length=20)


class CheatReportRequest(BaseModel):
    reason: str = Field(..., max_length=500)


class AbsenceStartRequest(BaseModel):
    reason: str = Field(..., max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=4, max_length=72)


class FeedbackRequest(BaseModel):
    category: Literal["bug", "suggestion", "general"]
    title: str = Field(..., max_length=100)
    body: str = Field(..., max_length=2000)


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
    now = datetime.now()

    att, att_date = await get_active_attendance(session, username)
    if not att:
        raise HTTPException(status_code=400, detail="출근 기록이 없습니다")

    # 미종료 외출 자동 종료
    absence_result = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == att_date, Absence.end_at == None
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
    now = datetime.now()

    att, att_date = await get_active_attendance(session, username)
    if not att:
        raise HTTPException(status_code=400, detail="출근 중이 아닙니다")

    existing = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == att_date, Absence.end_at == None
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 외출 중입니다")

    absence = Absence(username=username, date=att_date, start_at=now, reason=req.reason)
    session.add(absence)
    await session.commit()
    return {"status": "ok", "start_at": now.strftime("%H:%M")}


@router.post("/absence/end")
async def end_absence(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    now = datetime.now()

    att, att_date = await get_active_attendance(session, username)
    result = await session.execute(
        select(Absence).where(
            Absence.username == username, Absence.date == att_date, Absence.end_at == None
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
    now = datetime.now()

    att, att_date = await get_active_attendance(session, username)
    if not att:
        raise HTTPException(status_code=400, detail="출근 중이 아닙니다")

    result = await session.execute(
        select(ActivityLog).where(ActivityLog.username == username, ActivityLog.date == att_date)
    )
    log = result.scalar_one_or_none()

    if log:
        log.active_seconds += req.active_seconds
        log.last_updated = now
    else:
        log = ActivityLog(username=username, date=att_date, active_seconds=req.active_seconds)
        session.add(log)

    if req.client_version:
        user_result = await session.execute(select(User).where(User.username == username))
        user = user_result.scalar_one_or_none()
        if user:
            user.client_version = req.client_version

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
    days: int = Query(default=30, ge=1, le=365),
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


@router.get("/my-absence-stats")
async def my_absence_stats(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    end_d = date.today()
    start_d = end_d - timedelta(days=days - 1)

    result = await session.execute(
        select(Absence).where(
            Absence.username == username,
            Absence.date.between(start_d.isoformat(), end_d.isoformat()),
            Absence.end_at.isnot(None),
        )
    )
    absences = result.scalars().all()

    total_minutes = sum((a.end_at - a.start_at).total_seconds() / 60 for a in absences)
    days_with_absence = len(set(a.date for a in absences))
    count = len(absences)

    return {
        "days": days,
        "total_absence_minutes": round(total_minutes),
        "total_absence_count": count,
        "days_with_absence": days_with_absence,
        "avg_per_outing": round(total_minutes / count) if count > 0 else 0,
    }


@router.get("/groups")
async def get_groups(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_user),
):
    result = await session.execute(select(Group).order_by(Group.name))
    return [{"id": g.id, "name": g.name} for g in result.scalars().all()]


@router.get("/stats")
async def get_stats(
    target_date: Optional[str] = None,
    period: str = "daily",
    group_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    d = target_date or date.today().isoformat()
    try:
        d_obj = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜 형식이 올바르지 않습니다 (YYYY-MM-DD)")

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

    user_info = {
        row.username: {"group_id": row.group_id, "animal_type": row.animal_type}
        for row in (await session.execute(select(User.username, User.group_id, User.animal_type))).all()
    }
    user_groups = {u: v["group_id"] for u, v in user_info.items()}

    # group_id 필터 적용
    if group_id is not None:
        rows = [r for r in rows if user_groups.get(r.username) == group_id]

    goals = (await session.execute(select(StudyGoal))).scalars().all()
    goal_by_user = {g.username: g.daily_target_minutes for g in goals if g.username}
    goal_by_group = {g.group_id: g.daily_target_minutes for g in goals if not g.username}
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
        uid = user_groups.get(row.username)
        daily_goal = goal_by_user.get(row.username) or goal_by_group.get(uid, default_goal)
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
            "animal_type": user_info.get(row.username, {}).get("animal_type"),
        })
    return result


@router.get("/notices")
async def get_notices(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    # 유저의 group_id 조회
    user_result = await session.execute(select(User).where(User.username == current["sub"]))
    user = user_result.scalar_one_or_none()
    user_group_id = user.group_id if user else None

    from sqlalchemy import or_
    result = await session.execute(
        select(Notice).where(
            Notice.is_active == True,
            or_(Notice.group_id == None, Notice.group_id == user_group_id)
        ).order_by(Notice.created_at.desc())
    )
    notices = result.scalars().all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in notices
    ]


@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    if req.category not in ("bug", "suggestion", "general"):
        raise HTTPException(status_code=400, detail="카테고리는 bug/suggestion/general 중 하나여야 합니다")
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="제목을 입력해주세요")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="내용을 입력해주세요")
    feedback = Feedback(username=username, category=req.category, title=req.title.strip(), body=req.body.strip())
    session.add(feedback)
    await session.commit()
    return {"message": "피드백이 등록되었습니다"}
