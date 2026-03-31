from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, Field
from typing import Optional
from models import User, ActivityLog, Attendance, Absence, Group, CheatLog, StudyGoal, Feedback, Notice
from auth import hash_password, get_current_admin, get_current_superadmin
from database import get_session
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/admin", tags=["admin"])


# ── 그룹 관리 (슈퍼 어드민 전용) ───────────────────────

class CreateGroupRequest(BaseModel):
    name: str


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

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "member"
    group_id: Optional[int] = None


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None
    group_id: Optional[int] = None
    animal_type: Optional[int] = None  # 0~7 설정, -1이면 자동(초기화)


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
        }
        for u in users
    ]


@router.post("/users")
async def create_user(
    req: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(get_current_admin),
):
    if req.role == "superadmin":
        raise HTTPException(status_code=403, detail="슈퍼 어드민 계정은 생성할 수 없습니다")

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
        if req.animal_type != -1 and not (0 <= req.animal_type <= 7):
            raise HTTPException(status_code=400, detail="animal_type은 -1(자동) 또는 0~7이어야 합니다")
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
    active_seconds: float

class ActivityCreateRequest(BaseModel):
    username: str
    date: str
    active_seconds: float


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


# ── 목표 시간 관리 ──────────────────────────────────────

class GoalRequest(BaseModel):
    group_id: Optional[int] = None
    daily_target_minutes: int


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
            "group_id": g.group_id,
            "group_name": group_names.get(g.group_id, "–") if g.group_id else "전체 기본",
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
        if req.group_id != current.get("group_id"):
            raise HTTPException(status_code=403, detail="자기 그룹 목표만 설정할 수 있습니다")

    result = await session.execute(
        select(StudyGoal).where(StudyGoal.group_id == req.group_id)
    )
    goal = result.scalar_one_or_none()
    if goal:
        goal.daily_target_minutes = req.daily_target_minutes
    else:
        goal = StudyGoal(group_id=req.group_id, daily_target_minutes=req.daily_target_minutes)
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
            "created_at": fb.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for fb in feedbacks
    ]


@router.delete("/feedbacks/{feedback_id}")
async def delete_feedback(
    feedback_id: int,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Feedback).where(Feedback.id == feedback_id))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="피드백을 찾을 수 없습니다")
    await session.delete(fb)
    await session.commit()
    return {"message": "삭제되었습니다"}


# ── 공지 관리 ────────────────────────────────────────────

class NoticeRequest(BaseModel):
    title: str = Field(..., max_length=100)
    body: str = Field(..., max_length=2000)


@router.get("/notices")
async def list_notices(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(get_current_admin),
):
    result = await session.execute(select(Notice).order_by(Notice.created_at.desc()))
    notices = result.scalars().all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "is_active": n.is_active,
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
    notice = Notice(title=req.title.strip(), body=req.body.strip())
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
