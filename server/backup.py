import asyncio
import shutil
import os
import glob
from datetime import datetime, timedelta
from sqlalchemy import select
from database import get_session
from models import Attendance, Absence

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
