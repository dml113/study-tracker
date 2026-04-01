import asyncio
import shutil
import os
import glob
from datetime import datetime, timedelta, date
from sqlalchemy import select, func
from database import get_session
from models import Attendance, Absence, ActivityLog, User, Group, Notice

BACKUP_DIR = "backups"
DB_PATH = "study_tracker.db"
MAX_BACKUPS = 7


async def run_backup() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        return ""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"study_tracker_{timestamp}.db")
    shutil.copy2(DB_PATH, dest)
    _cleanup_old_backups()
    return dest


def _cleanup_old_backups():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "study_tracker_*.db")))
    for old_file in files[:-MAX_BACKUPS]:
        os.remove(old_file)


def list_backups() -> list[dict]:
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "study_tracker_*.db")), reverse=True)
    result = []
    for f in files:
        stat = os.stat(f)
        result.append({
            "filename": os.path.basename(f),
            "size_kb": round(stat.st_size / 1024, 1),
            "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return result


async def backup_scheduler():
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((tomorrow - now).total_seconds())
        try:
            await run_backup()
            print(f"[백업] 자동 백업 완료 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        except Exception as e:
            print(f"[백업] 자동 백업 실패: {e}")


async def run_auto_checkout():
    """출근 중인 모든 유저를 자동 퇴근 처리."""
    now = datetime.now()
    today = now.date().isoformat()
    count = 0
    async for session in get_session():
        try:
            att_result = await session.execute(
                select(Attendance).where(
                    Attendance.date == today,
                    Attendance.checkin_at != None,
                    Attendance.checkout_at == None,
                )
            )
            atts = att_result.scalars().all()
            for att in atts:
                # 미종료 외출 자동 종료
                absence_result = await session.execute(
                    select(Absence).where(
                        Absence.username == att.username,
                        Absence.date == today,
                        Absence.end_at == None,
                    )
                )
                active_absence = absence_result.scalar_one_or_none()
                if active_absence:
                    active_absence.end_at = now
                att.checkout_at = now
                count += 1
            await session.commit()
        except Exception as e:
            print(f"[자동퇴근] 오류: {e}")
    return count


async def generate_weekly_report():
    """지난주 (월~일) 랭킹 리포트를 공지로 등록."""
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)

    async for session in get_session():
        try:
            rows = (await session.execute(
                select(ActivityLog.username, func.sum(ActivityLog.active_seconds).label("total"))
                .where(ActivityLog.date.between(last_monday.isoformat(), last_sunday.isoformat()))
                .group_by(ActivityLog.username)
                .order_by(func.sum(ActivityLog.active_seconds).desc())
            )).all()

            if not rows:
                return

            # 그룹별 집계
            user_info = {
                row.username: row.group_id
                for row in (await session.execute(select(User.username, User.group_id))).all()
            }
            group_names = {g.id: g.name for g in (await session.execute(select(Group))).scalars().all()}

            group_totals: dict = {}
            group_counts: dict = {}
            for row in rows:
                gid = user_info.get(row.username)
                gname = group_names.get(gid, "미배정") if gid else "미배정"
                group_totals[gname] = group_totals.get(gname, 0) + (row.total or 0)
                group_counts[gname] = group_counts.get(gname, 0) + 1

            medals = ["🥇", "🥈", "🥉"]
            ranking_lines = []
            for i, row in enumerate(rows[:10]):
                mins = int((row.total or 0) // 60)
                medal = medals[i] if i < 3 else f"{i+1}."
                ranking_lines.append(f"{medal} {row.username}  {mins}분")

            group_lines = []
            for gname, total in sorted(group_totals.items(), key=lambda x: -x[1]):
                avg = int(total // 60 // max(group_counts[gname], 1))
                group_lines.append(f"• {gname}: 평균 {avg}분")

            period_str = f"{last_monday.strftime('%m/%d')}~{last_sunday.strftime('%m/%d')}"
            body = "📊 개인 랭킹 (상위 10명)\n"
            body += "\n".join(ranking_lines)
            body += "\n\n🏠 그룹별 평균\n"
            body += "\n".join(group_lines)

            notice = Notice(
                title=f"주간 리포트 ({period_str})",
                body=body,
                is_active=True,
            )
            session.add(notice)
            await session.commit()
            print(f"[주간리포트] {period_str} 리포트 공지 등록 완료")
        except Exception as e:
            print(f"[주간리포트] 실패: {e}")


async def weekly_report_scheduler():
    """매주 월요일 07:00에 지난주 리포트 자동 공지."""
    while True:
        now = datetime.now()
        # 오늘이 월요일이고 아직 07:00 이전이면 오늘 07:00에 실행
        if now.weekday() == 0 and now.hour < 7:
            next_monday = now.replace(hour=7, minute=0, second=0, microsecond=0)
        else:
            days_until_monday = (7 - now.weekday()) % 7 or 7
            next_monday = (now + timedelta(days=days_until_monday)).replace(hour=7, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_monday - now).total_seconds())
        try:
            await generate_weekly_report()
        except Exception as e:
            print(f"[주간리포트] 스케줄러 오류: {e}")


async def auto_checkout_scheduler():
    """매일 00:50에 미퇴근 인원 자동 퇴근."""
    while True:
        now = datetime.now()
        target = now.replace(hour=0, minute=50, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            count = await run_auto_checkout()
            print(f"[자동퇴근] {count}명 자동 퇴근 처리 완료 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        except Exception as e:
            print(f"[자동퇴근] 실패: {e}")
