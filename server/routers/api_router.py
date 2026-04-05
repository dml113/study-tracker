from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Literal, Optional
from models import ActivityLog, Attendance, Absence, CheatLog, User, StudyGoal, Feedback, Notice, Group, UserPoint, PointLog, ShopItem, UserInventory, UserEquip
from auth import get_current_user, verify_password, hash_password
from database import get_session
from datetime import date, datetime, timedelta
import asyncio
from backup import _post_slack

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

    # 연속 출석 보너스
    streak_bonus = 0
    streak = 0
    check_date = date.today()
    for _ in range(32):
        check_date -= timedelta(days=1)
        prev_result = await session.execute(
            select(Attendance).where(
                Attendance.username == username,
                Attendance.date == check_date.isoformat(),
                Attendance.checkin_at != None,
            )
        )
        if prev_result.scalar_one_or_none():
            streak += 1
        else:
            break

    if streak in (2, 6, 13, 29):  # 3일, 7일, 14일, 30일째
        bonus_map = {2: 5, 6: 10, 13: 20, 29: 50}
        streak_bonus = bonus_map[streak]
        up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
        user_point = up_result.scalar_one_or_none()
        if not user_point:
            user_point = UserPoint(username=username)
            session.add(user_point)
        user_point.points += streak_bonus
        session.add(PointLog(username=username, amount=streak_bonus, reason="streak"))

    await session.commit()
    return {"status": "ok", "checkin_at": now.strftime("%H:%M"), "streak_bonus": streak_bonus, "streak_days": streak + 1}


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

    # 포인트 적립: 360초(6분)당 1포인트
    SECONDS_PER_POINT = 360
    up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
    user_point = up_result.scalar_one_or_none()
    if not user_point:
        user_point = UserPoint(username=username)
        session.add(user_point)
    user_point.seconds_buffer += req.active_seconds
    earned = int(user_point.seconds_buffer // SECONDS_PER_POINT)
    if earned > 0:
        user_point.seconds_buffer -= earned * SECONDS_PER_POINT
        user_point.points += earned
        user_point.updated_at = now
        session.add(PointLog(username=username, amount=earned, reason="study"))

    await session.commit()
    return {"status": "ok", "points_earned": earned if earned > 0 else 0}


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
    slack_text = f"🚨 어머! {username}님한테 수상한 입력이 감지됐어요!\n사유: {req.reason}\n시각: {datetime.now().strftime('%H:%M')}\n확인 부탁드려요 👀"
    await asyncio.to_thread(_post_slack, slack_text, "Study-Alert", ":rotating_light:")
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


@router.get("/my-lifetime")
async def my_lifetime(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    result = await session.execute(
        select(func.sum(ActivityLog.active_seconds)).where(ActivityLog.username == username)
    )
    total_secs = result.scalar() or 0
    return {"lifetime_minutes": round(total_secs / 60, 1)}


@router.get("/my-feedbacks")
async def my_feedbacks(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    result = await session.execute(
        select(Feedback).where(Feedback.username == username).order_by(Feedback.created_at.desc())
    )
    feedbacks = result.scalars().all()
    cat_map = {"bug": "🐛 버그", "suggestion": "✨ 기능 제안", "general": "💬 기타"}
    return [
        {
            "id": f.id,
            "category": f.category,
            "category_label": cat_map.get(f.category, f.category),
            "title": f.title,
            "body": f.body,
            "is_resolved": f.is_resolved,
            "admin_comment": f.admin_comment,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for f in feedbacks
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


# ── 포인트 ────────────────────────────────────────────────────────

@router.get("/my-points")
async def my_points(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
    user_point = up_result.scalar_one_or_none()
    points = user_point.points if user_point else 0

    logs_result = await session.execute(
        select(PointLog).where(PointLog.username == username).order_by(PointLog.created_at.desc()).limit(20)
    )
    logs = logs_result.scalars().all()
    reason_map = {"study": "📚 공부", "streak": "🔥 연속 출석", "ranking": "🏆 랭킹 보너스", "purchase": "🛍️ 아이템 구매"}
    return {
        "points": points,
        "logs": [
            {
                "amount": l.amount,
                "reason": reason_map.get(l.reason, l.reason),
                "created_at": l.created_at.strftime("%m/%d %H:%M"),
            }
            for l in logs
        ],
    }


# ── 상점 ────────────────────────────────────────────────────────

@router.get("/shop")
async def get_shop(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    items_result = await session.execute(
        select(ShopItem).where(ShopItem.is_active == True).order_by(ShopItem.slot, ShopItem.price)
    )
    items = items_result.scalars().all()

    owned_result = await session.execute(
        select(UserInventory.item_id).where(UserInventory.username == username)
    )
    owned_ids = {row[0] for row in owned_result.all()}

    up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
    user_point = up_result.scalar_one_or_none()
    my_points = user_point.points if user_point else 0

    slot_map = {"hat": "🎩 모자", "top": "👕 상의", "accessory": "✨ 액세서리"}
    return {
        "my_points": my_points,
        "items": [
            {
                "id": i.id,
                "name": i.name,
                "slot": i.slot,
                "slot_label": slot_map.get(i.slot, i.slot),
                "price": i.price,
                "svg_data": i.svg_data,
                "owned": i.id in owned_ids,
            }
            for i in items
        ],
    }


@router.post("/shop/buy/{item_id}")
async def buy_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]

    item_result = await session.execute(select(ShopItem).where(ShopItem.id == item_id, ShopItem.is_active == True))
    item = item_result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="아이템을 찾을 수 없어요")

    owned_result = await session.execute(
        select(UserInventory).where(UserInventory.username == username, UserInventory.item_id == item_id)
    )
    if owned_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 보유 중인 아이템이에요")

    up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
    user_point = up_result.scalar_one_or_none()
    if not user_point or user_point.points < item.price:
        raise HTTPException(status_code=400, detail="포인트가 부족해요")

    user_point.points -= item.price
    session.add(PointLog(username=username, amount=-item.price, reason="purchase"))
    session.add(UserInventory(username=username, item_id=item_id))
    await session.commit()
    return {"message": f"{item.name} 구매 완료!", "remaining_points": user_point.points}


# ── 인벤토리 / 장비 ────────────────────────────────────────────────

@router.get("/inventory")
async def get_inventory(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    inv_result = await session.execute(
        select(UserInventory, ShopItem)
        .join(ShopItem, UserInventory.item_id == ShopItem.id)
        .where(UserInventory.username == username)
        .order_by(ShopItem.slot)
    )
    rows = inv_result.all()

    equip_result = await session.execute(select(UserEquip).where(UserEquip.username == username))
    equips = {e.slot: e.item_id for e in equip_result.scalars().all()}

    slot_map = {"hat": "🎩 모자", "top": "👕 상의", "accessory": "✨ 액세서리"}
    return [
        {
            "inventory_id": inv.id,
            "item_id": item.id,
            "name": item.name,
            "slot": item.slot,
            "slot_label": slot_map.get(item.slot, item.slot),
            "svg_data": item.svg_data,
            "equipped": equips.get(item.slot) == item.id,
        }
        for inv, item in rows
    ]


@router.get("/equip")
async def get_equip(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]
    equip_result = await session.execute(
        select(UserEquip, ShopItem)
        .join(ShopItem, UserEquip.item_id == ShopItem.id)
        .where(UserEquip.username == username, UserEquip.item_id != None)
    )
    rows = equip_result.all()
    return {e.slot: {"item_id": item.id, "name": item.name, "svg_data": item.svg_data} for e, item in rows}


class EquipRequest(BaseModel):
    slot: Literal["hat", "top", "accessory"]
    item_id: Optional[int] = None  # None이면 해제


@router.post("/equip")
async def equip_item(
    req: EquipRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_user),
):
    username = current["sub"]

    if req.item_id is not None:
        owned_result = await session.execute(
            select(UserInventory).where(UserInventory.username == username, UserInventory.item_id == req.item_id)
        )
        if not owned_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="보유하지 않은 아이템이에요")

        item_result = await session.execute(select(ShopItem).where(ShopItem.id == req.item_id))
        item = item_result.scalar_one_or_none()
        if not item or item.slot != req.slot:
            raise HTTPException(status_code=400, detail="슬롯이 맞지 않아요")

    equip_result = await session.execute(
        select(UserEquip).where(UserEquip.username == username, UserEquip.slot == req.slot)
    )
    equip = equip_result.scalar_one_or_none()
    if equip:
        equip.item_id = req.item_id
    else:
        equip = UserEquip(username=username, slot=req.slot, item_id=req.item_id)
        session.add(equip)

    await session.commit()
    return {"message": "착용 변경 완료"}
