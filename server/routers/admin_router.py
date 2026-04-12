from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Optional
from models import User, ActivityLog, Attendance, Absence, Group, CheatLog, StudyGoal, Feedback, Notice, ShopItem, UserInventory, UserEquip, UserPoint, PointLog
from auth import hash_password, get_current_admin, get_current_superadmin
from database import get_session
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/admin", tags=["admin"])


# ── 그룹 관리 (슈퍼 어드민 전용) ───────────────────────

class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)


@router.get("/groups")
async def list_groups(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Group).order_by(Group.name))
    groups = result.scalars().all()
    return [{"id": g.id, "name": g.name, "created_at": g.created_at.isoformat()} for g in groups]


@router.post("/groups")
async def create_group(
    req: CreateGroupRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    existing = await session.execute(select(Group).where(Group.name == req.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 존재하는 그룹명입니다")
    group = Group(name=req.name)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return {"message": f"'{req.name}' 그룹이 생성되었습니다", "id": group.id}


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="그룹을 찾을 수 없습니다")

    # 소속 유저 group_id 해제
    users_result = await session.execute(select(User).where(User.group_id == group_id))
    for user in users_result.scalars().all():
        user.group_id = None

    await session.delete(group)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 유저 관리 ──────────────────────────────────────────

_ALLOWED_ROLES = {"member", "group_admin"}

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=30, pattern=r'^[a-zA-Z0-9_가-힣\-]+$')
    password: str = Field(..., min_length=4, max_length=72)
    role: str = Field(default="member")
    group_id: Optional[int] = None



class UpdateUserRequest(BaseModel):
    password: Optional[str] = Field(None, min_length=4, max_length=72)
    is_active: Optional[bool] = None
    role: Optional[str] = None
    group_id: Optional[int] = None
    animal_type: Optional[int] = None  # 0~8 설정, -1이면 자동(초기화)


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    query = select(User).order_by(User.created_at)
    if current["role"] == "group_admin":
        query = query.where(User.group_id == current.get("group_id"))

    users_result = await session.execute(query)
    users = users_result.scalars().all()

    today = date.today().isoformat()
    stats_result = await session.execute(
        select(ActivityLog.username, ActivityLog.active_seconds).where(ActivityLog.date == today)
    )
    today_stats = {row.username: row.active_seconds for row in stats_result}

    # 마지막 활동일 조회
    last_act_result = await session.execute(
        select(ActivityLog.username, func.max(ActivityLog.date).label("last_date"))
        .group_by(ActivityLog.username)
    )
    last_activity = {row.username: row.last_date for row in last_act_result}

    groups_result = await session.execute(select(Group))
    group_names = {g.id: g.name for g in groups_result.scalars().all()}

    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "group_id": u.group_id,
            "group_name": group_names.get(u.group_id) if u.group_id else None,
            "is_active": u.is_active,
            "animal_type": u.animal_type,
            "client_version": u.client_version,
            "created_at": u.created_at.isoformat(),
            "today_seconds": today_stats.get(u.username, 0),
            "today_minutes": round(today_stats.get(u.username, 0) / 60, 1),
            "last_activity_date": last_activity.get(u.username),
        }
        for u in users
    ]


@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    if req.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"role은 {_ALLOWED_ROLES} 중 하나여야 합니다")

    if current["role"] == "group_admin":
        if req.role != "member":
            raise HTTPException(status_code=403, detail="그룹 관리자는 일반 유저만 생성할 수 있습니다")
        req.group_id = current.get("group_id")

    existing = await session.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자명입니다")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
        group_id=req.group_id,
    )
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

    if current["role"] == "group_admin":
        if user.group_id != current.get("group_id"):
            raise HTTPException(status_code=403, detail="다른 그룹의 유저는 수정할 수 없습니다")
        if user.role != "member":
            raise HTTPException(status_code=403, detail="관리자 계정은 수정할 수 없습니다")
        if req.role is not None and req.role != "member":
            raise HTTPException(status_code=403, detail="그룹 관리자는 권한을 변경할 수 없습니다")
        req.group_id = None  # 그룹 이동 불가

    if user.role == "superadmin" and req.role is not None and req.role != "superadmin":
        raise HTTPException(status_code=403, detail="슈퍼 어드민 권한은 변경할 수 없습니다")

    if req.password is not None:
        user.password_hash = hash_password(req.password)
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.role is not None:
        user.role = req.role
    if req.group_id is not None:
        user.group_id = req.group_id
    if req.animal_type is not None:
        if req.animal_type != -1 and not (0 <= req.animal_type <= 8):
            raise HTTPException(status_code=400, detail="animal_type은 -1(자동) 또는 0~8이어야 합니다")
        user.animal_type = None if req.animal_type == -1 else req.animal_type

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
    if user.role == "superadmin":
        raise HTTPException(status_code=403, detail="슈퍼 어드민 계정은 삭제할 수 없습니다")

    if current["role"] == "group_admin":
        if user.group_id != current.get("group_id"):
            raise HTTPException(status_code=403, detail="다른 그룹의 유저는 삭제할 수 없습니다")
        if user.role != "member":
            raise HTTPException(status_code=403, detail="관리자 계정은 삭제할 수 없습니다")

    await session.delete(user)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 공부 기록 수정 ─────────────────────────────────────

class ActivityEditRequest(BaseModel):
    active_seconds: float = Field(..., ge=0)

class ActivityCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=30)
    date: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')
    active_seconds: float = Field(..., ge=0)


@router.get("/activity")
async def get_activity(
    target_date: Optional[str] = None,
    username: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    d = target_date or date.today().isoformat()
    existing_usernames = set(
        (await session.execute(select(User.username))).scalars().all()
    )
    query = select(ActivityLog).where(ActivityLog.date == d)
    if username:
        query = query.where(ActivityLog.username == username)
    result = await session.execute(query.order_by(ActivityLog.active_seconds.desc()))
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "username": log.username,
            "date": log.date,
            "active_seconds": log.active_seconds,
            "active_minutes": round(log.active_seconds / 60, 1),
            "last_updated": log.last_updated.isoformat(),
        }
        for log in logs
        if log.username in existing_usernames
    ]


@router.patch("/activity/{log_id}")
async def update_activity(
    log_id: int,
    req: ActivityEditRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(ActivityLog).where(ActivityLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다")
    if req.active_seconds < 0:
        raise HTTPException(status_code=400, detail="활동 시간은 0 이상이어야 합니다")
    log.active_seconds = req.active_seconds
    log.last_updated = datetime.now()
    await session.commit()
    return {"message": "수정되었습니다"}


@router.post("/activity")
async def create_activity(
    req: ActivityCreateRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    if req.active_seconds < 0:
        raise HTTPException(status_code=400, detail="활동 시간은 0 이상이어야 합니다")
    existing = await session.execute(
        select(ActivityLog).where(ActivityLog.username == req.username, ActivityLog.date == req.date)
    )
    log = existing.scalar_one_or_none()
    if log:
        log.active_seconds = req.active_seconds
        log.last_updated = datetime.now()
    else:
        log = ActivityLog(username=req.username, date=req.date, active_seconds=req.active_seconds)
        session.add(log)
    await session.commit()
    return {"message": "저장되었습니다"}


# ── 출퇴근 기록 ────────────────────────────────────────

@router.get("/attendance")
async def get_attendance(
    target_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    today = target_date or date.today().isoformat()

    query = select(User).order_by(User.username)
    if current["role"] == "group_admin":
        query = query.where(User.group_id == current.get("group_id"))

    users_result = await session.execute(query)
    users = users_result.scalars().all()
    usernames = {u.username for u in users}

    att_result = await session.execute(select(Attendance).where(Attendance.date == today))
    attendances = {a.username: a for a in att_result.scalars().all() if a.username in usernames}

    stats_result = await session.execute(
        select(ActivityLog.username, ActivityLog.active_seconds).where(ActivityLog.date == today)
    )
    stats = {row.username: row.active_seconds for row in stats_result if row.username in usernames}

    absence_result = await session.execute(
        select(Absence).where(Absence.date == today).order_by(Absence.start_at)
    )
    absences_by_user: dict[str, list] = {}
    for ab in absence_result.scalars().all():
        if ab.username in usernames:
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


# ── 그룹별 통계 ────────────────────────────────────────

@router.get("/group-stats")
async def get_group_stats(
    target_date: Optional[str] = None,
    period: str = "daily",
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
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

    # 유저 그룹 정보
    query = select(User.username, User.group_id)
    if current["role"] == "group_admin":
        query = query.where(User.group_id == current.get("group_id"))
    user_rows = (await session.execute(query)).all()
    user_group_map = {r.username: r.group_id for r in user_rows}

    groups_result = await session.execute(select(Group))
    group_names = {g.id: g.name for g in groups_result.scalars().all()}

    # 활동 데이터
    act_rows = (await session.execute(
        select(ActivityLog.username, func.sum(ActivityLog.active_seconds).label("total"))
        .where(ActivityLog.date.between(start.isoformat(), end.isoformat()))
        .group_by(ActivityLog.username)
    )).all()

    group_totals: dict = {}
    group_members: dict = {}
    group_active: dict = {}
    group_top: dict = {}

    for r in act_rows:
        gid = user_group_map.get(r.username)
        if gid is None:
            continue
        total = r.total or 0
        if gid not in group_totals:
            group_totals[gid] = 0
            group_active[gid] = 0
            group_top[gid] = (None, 0)
        group_totals[gid] += total
        group_active[gid] += 1
        if total > group_top[gid][1]:
            group_top[gid] = (r.username, total)

    for r in user_rows:
        gid = r.group_id
        if gid is None:
            continue
        group_members[gid] = group_members.get(gid, 0) + 1

    # 목표 조회
    goals = (await session.execute(select(StudyGoal))).scalars().all()
    goal_by_group = {g.group_id: g.daily_target_minutes for g in goals if not g.username}
    default_goal = goal_by_group.get(None, 480)

    result = []
    for gid, gname in group_names.items():
        if current["role"] == "group_admin" and gid != current.get("group_id"):
            continue
        total_secs = group_totals.get(gid, 0)
        member_count = group_members.get(gid, 0)
        active_count = group_active.get(gid, 0)
        daily_goal = goal_by_group.get(gid, default_goal)
        period_goal = daily_goal * period_days * max(member_count, 1)
        avg_minutes = round(total_secs / 60 / max(member_count, 1), 1)
        top_user, top_secs = group_top.get(gid, (None, 0))
        result.append({
            "group_id": gid,
            "group_name": gname,
            "total_minutes": round(total_secs / 60, 1),
            "avg_minutes": avg_minutes,
            "member_count": member_count,
            "active_count": active_count,
            "daily_goal_minutes": daily_goal,
            "achievement_rate": round(total_secs / 60 / max(period_goal, 1) * 100, 1),
            "top_user": top_user,
            "top_user_minutes": round(top_secs / 60, 1),
        })
    result.sort(key=lambda x: -x["total_minutes"])
    return result


# ── 목표 시간 관리 ──────────────────────────────────────

class GoalRequest(BaseModel):
    username: Optional[str] = Field(None, max_length=30)  # 개인 목표
    group_id: Optional[int] = None
    daily_target_minutes: int = Field(..., ge=1, le=1440)


@router.get("/goals")
async def list_goals(
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    result = await session.execute(select(StudyGoal))
    goals = result.scalars().all()

    groups_result = await session.execute(select(Group))
    group_names = {g.id: g.name for g in groups_result.scalars().all()}

    return [
        {
            "id": g.id,
            "username": g.username,
            "group_id": g.group_id,
            "group_name": group_names.get(g.group_id, "–") if g.group_id else ("전체 기본" if not g.username else None),
            "daily_target_minutes": g.daily_target_minutes,
        }
        for g in goals
    ]


@router.post("/goals")
async def upsert_goal(
    req: GoalRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    if current["role"] == "group_admin":
        if req.username:
            raise HTTPException(status_code=403, detail="그룹 관리자는 개인 목표를 설정할 수 없습니다")
        if req.group_id != current.get("group_id"):
            raise HTTPException(status_code=403, detail="자기 그룹 목표만 설정할 수 있습니다")

    if req.username:
        # 유저 존재 확인
        user_check = await session.execute(select(User).where(User.username == req.username))
        if not user_check.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="존재하지 않는 유저입니다")
        # 개인 목표: username 기준으로 upsert
        result = await session.execute(
            select(StudyGoal).where(StudyGoal.username == req.username)
        )
    else:
        result = await session.execute(
            select(StudyGoal).where(StudyGoal.username.is_(None), StudyGoal.group_id == req.group_id)
        )
    goal = result.scalar_one_or_none()
    if goal:
        goal.daily_target_minutes = req.daily_target_minutes
    else:
        goal = StudyGoal(username=req.username, group_id=req.group_id, daily_target_minutes=req.daily_target_minutes)
        session.add(goal)
    await session.commit()
    return {"message": "목표 시간이 설정되었습니다"}


@router.delete("/goals/{goal_id}")
async def delete_goal(
    goal_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(StudyGoal).where(StudyGoal.id == goal_id))
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="목표를 찾을 수 없습니다")
    await session.delete(goal)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 외출 통계 ───────────────────────────────────────────

@router.get("/absence-stats")
async def get_absence_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    today = date.today()
    end_d = date.fromisoformat(end_date) if end_date else today
    start_d = date.fromisoformat(start_date) if start_date else today - timedelta(days=6)

    query = select(Absence).where(Absence.date.between(start_d.isoformat(), end_d.isoformat()))
    if current["role"] == "group_admin":
        users_result = await session.execute(
            select(User.username).where(User.group_id == current.get("group_id"))
        )
        group_usernames = {row[0] for row in users_result.all()}
        query = query.where(Absence.username.in_(group_usernames))

    result = await session.execute(query)
    absences = result.scalars().all()

    reason_counts: dict = {}
    user_counts: dict = {}
    user_minutes: dict = {}

    for ab in absences:
        reason_counts[ab.reason] = reason_counts.get(ab.reason, 0) + 1
        user_counts[ab.username] = user_counts.get(ab.username, 0) + 1
        if ab.start_at and ab.end_at:
            minutes = (ab.end_at - ab.start_at).total_seconds() / 60
            user_minutes[ab.username] = user_minutes.get(ab.username, 0) + minutes

    total = len(absences)
    by_reason = sorted(
        [{"reason": r, "count": c, "percentage": round(c / total * 100, 1) if total else 0}
         for r, c in reason_counts.items()],
        key=lambda x: -x["count"],
    )
    by_user = sorted(
        [{"username": u, "count": user_counts[u], "total_minutes": round(user_minutes.get(u, 0), 1)}
         for u in user_counts],
        key=lambda x: -x["count"],
    )

    return {
        "period": {"start": start_d.isoformat(), "end": end_d.isoformat()},
        "total_count": total,
        "by_reason": by_reason,
        "by_user": by_user,
    }


# ── 치트 기록 ──────────────────────────────────────────

@router.get("/cheats")
async def get_cheats(
    target_date: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    today = target_date or date.today().isoformat()

    query = select(CheatLog).where(CheatLog.date == today).order_by(CheatLog.detected_at.desc())
    if current["role"] == "group_admin":
        # 자기 그룹 유저만
        users_result = await session.execute(
            select(User.username).where(User.group_id == current.get("group_id"))
        )
        group_usernames = {row[0] for row in users_result.all()}
        query = query.where(CheatLog.username.in_(group_usernames))

    result = await session.execute(query)
    logs = result.scalars().all()
    return [
        {
            "username": log.username,
            "detected_at": log.detected_at.strftime("%H:%M:%S"),
            "reason": log.reason,
        }
        for log in logs
    ]


# ── 피드백 조회 ────────────────────────────────────────

@router.get("/feedbacks")
async def get_feedbacks(
    category: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    if category and category not in ("bug", "suggestion", "general"):
        raise HTTPException(status_code=400, detail="category는 bug/suggestion/general 중 하나여야 합니다")
    query = select(Feedback).order_by(Feedback.created_at.desc())
    if category:
        query = query.where(Feedback.category == category)
    result = await session.execute(query)
    feedbacks = result.scalars().all()
    return [
        {
            "id": fb.id,
            "username": fb.username,
            "category": fb.category,
            "title": fb.title,
            "body": fb.body,
            "is_resolved": fb.is_resolved,
            "admin_comment": fb.admin_comment,
            "created_at": fb.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for fb in feedbacks
    ]


class FeedbackResolveRequest(BaseModel):
    is_resolved: bool
    admin_comment: Optional[str] = Field(None, max_length=500)


@router.patch("/feedbacks/{feedback_id}/resolve")
async def resolve_feedback(
    feedback_id: int,
    req: FeedbackResolveRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="피드백을 찾을 수 없습니다")
    # group_admin은 자기 그룹 유저의 피드백만 처리 가능
    if current["role"] == "group_admin":
        user_result = await session.execute(
            select(User).where(User.username == fb.username, User.group_id == current.get("group_id"))
        )
        if not user_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="다른 그룹의 피드백은 처리할 수 없습니다")
    fb.is_resolved = req.is_resolved
    fb.admin_comment = req.admin_comment
    await session.commit()
    return {"message": "처리 상태가 업데이트되었습니다"}


@router.delete("/feedbacks/{feedback_id}")
async def delete_feedback(
    feedback_id: int,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="피드백을 찾을 수 없습니다")
    if current["role"] == "group_admin":
        user_result = await session.execute(
            select(User).where(User.username == fb.username, User.group_id == current.get("group_id"))
        )
        if not user_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="다른 그룹의 피드백은 삭제할 수 없습니다")
    await session.delete(fb)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 공지 관리 ────────────────────────────────────────────

class NoticeRequest(BaseModel):
    title: str = Field(..., max_length=100)
    body: str = Field(..., max_length=2000)
    group_id: Optional[int] = None  # None=전체, group_id=특정 그룹


@router.get("/notices")
async def list_notices(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Notice).order_by(Notice.created_at.desc()))
    notices = result.scalars().all()
    groups_result = await session.execute(select(Group))
    group_names = {g.id: g.name for g in groups_result.scalars().all()}
    return [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "is_active": n.is_active,
            "group_id": n.group_id,
            "group_name": group_names.get(n.group_id) if n.group_id else None,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in notices
    ]


@router.post("/notices")
async def create_notice(
    req: NoticeRequest,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="제목을 입력해주세요")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="내용을 입력해주세요")
    if req.group_id is not None:
        group_result = await session.execute(select(Group).where(Group.id == req.group_id))
        if not group_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="존재하지 않는 그룹입니다")
    notice = Notice(title=req.title.strip(), body=req.body.strip(), group_id=req.group_id)
    session.add(notice)
    await session.commit()
    return {"message": "공지가 등록되었습니다"}


@router.patch("/notices/{notice_id}/toggle")
async def toggle_notice(
    notice_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(Notice).where(Notice.id == notice_id))
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다")
    notice.is_active = not notice.is_active
    await session.commit()
    return {"message": "변경되었습니다", "is_active": notice.is_active}


@router.delete("/notices/{notice_id}")
async def delete_notice(
    notice_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(Notice).where(Notice.id == notice_id))
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다")
    await session.delete(notice)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 상점 관리 (superadmin) ────────────────────────────────────────

import re as _re

def _sanitize_svg(svg: str) -> str:
    """SVG에서 잠재적 XSS 요소 제거."""
    # <script> 태그 제거
    svg = _re.sub(r'<script[\s\S]*?</script>', '', svg, flags=_re.IGNORECASE)
    # <foreignObject> 제거 (HTML 삽입 가능)
    svg = _re.sub(r'<foreignObject[\s\S]*?</foreignObject>', '', svg, flags=_re.IGNORECASE)
    svg = _re.sub(r'<foreignObject[^>]*/>', '', svg, flags=_re.IGNORECASE)
    # on* 이벤트 핸들러 제거 (따옴표 있는/없는 모두)
    svg = _re.sub(r'\s+on\w+\s*=\s*(?:["\'][^"\']*["\']|[^\s>]+)', '', svg, flags=_re.IGNORECASE)
    # javascript: URL 제거
    svg = _re.sub(r'javascript\s*:', '', svg, flags=_re.IGNORECASE)
    # href/xlink:href에서 외부 URL 제거
    svg = _re.sub(r'(href|xlink:href)\s*=\s*["\'](?!#)[^"\']*["\']', '', svg, flags=_re.IGNORECASE)
    # <animate>/<set>의 href 속성 조작 차단
    svg = _re.sub(r'(<(?:animate|set)[^>]*\battributeName\s*=\s*["\'](?:href|xlink:href)["\'][^>]*>)', '', svg, flags=_re.IGNORECASE)
    # <use> 태그의 외부 href/xlink:href 제거 (이미 위에서 처리되지만 명시적으로)
    svg = _re.sub(r'(<use[^>]*)(xlink:href|href)\s*=\s*["\'][^#][^"\']*["\']', r'\1', svg, flags=_re.IGNORECASE)
    return svg.strip()


class ShopItemCreate(BaseModel):
    name: str = Field(..., max_length=50)
    slot: str = Field(..., pattern="^(hat|top|accessory)$")
    price: int = Field(..., gt=0, le=99999)
    svg_data: str = Field(..., min_length=10, max_length=50000)


class ShopItemUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    price: Optional[int] = Field(None, gt=0, le=99999)
    is_active: Optional[bool] = None


@router.get("/shop")
async def admin_get_shop(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(ShopItem).order_by(ShopItem.slot, ShopItem.price))
    items = result.scalars().all()
    slot_map = {"hat": "🎩 모자", "top": "👕 상의", "accessory": "✨ 액세서리"}
    return [
        {
            "id": i.id,
            "name": i.name,
            "slot": i.slot,
            "slot_label": slot_map.get(i.slot, i.slot),
            "price": i.price,
            "svg_data": i.svg_data,
            "is_active": i.is_active,
            "created_at": i.created_at.strftime("%Y-%m-%d"),
        }
        for i in items
    ]


@router.post("/shop")
async def admin_create_shop_item(
    req: ShopItemCreate,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    item = ShopItem(name=req.name, slot=req.slot, price=req.price, svg_data=_sanitize_svg(req.svg_data))
    session.add(item)
    await session.commit()
    return {"message": "아이템 등록 완료", "id": item.id}


@router.patch("/shop/{item_id}")
async def admin_update_shop_item(
    item_id: int,
    req: ShopItemUpdate,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(ShopItem).where(ShopItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="아이템을 찾을 수 없습니다")
    if req.name is not None:
        item.name = req.name
    if req.price is not None:
        item.price = req.price
    if req.is_active is not None:
        item.is_active = req.is_active
    await session.commit()
    return {"message": "수정 완료"}


@router.delete("/shop/{item_id}")
async def admin_delete_shop_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(select(ShopItem).where(ShopItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="아이템을 찾을 수 없습니다")
    # 관련 인벤토리/장비 레코드 먼저 정리
    await session.execute(
        UserEquip.__table__.delete().where(UserEquip.item_id == item_id)
    )
    await session.execute(
        UserInventory.__table__.delete().where(UserInventory.item_id == item_id)
    )
    await session.delete(item)
    await session.commit()
    return {"message": "삭제 완료"}


@router.get("/points")
async def admin_get_points(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    result = await session.execute(
        select(User.username, UserPoint.points)
        .outerjoin(UserPoint, User.username == UserPoint.username)
        .where(User.role != "superadmin")
        .order_by(func.coalesce(UserPoint.points, 0).desc(), User.username.asc())
    )
    return [{"username": row[0], "points": row[1] or 0} for row in result.all()]


class PointAdjust(BaseModel):
    username: str
    amount: int = Field(..., ge=-99999, le=99999)
    reason: str = Field(default="관리자 조정", max_length=100)


@router.post("/points/adjust")
async def admin_adjust_points(
    req: PointAdjust,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_superadmin),
):
    up_result = await session.execute(select(UserPoint).where(UserPoint.username == req.username))
    user_point = up_result.scalar_one_or_none()
    if not user_point:
        user_point = UserPoint(username=req.username, points=0)
        session.add(user_point)
    user_point.points = max(0, user_point.points + req.amount)
    session.add(PointLog(username=req.username, amount=req.amount, reason=req.reason))
    await session.commit()
    return {"message": "포인트 조정 완료", "new_points": user_point.points}
