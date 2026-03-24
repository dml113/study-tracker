import asyncio
import shutil
import os
import glob
from datetime import datetime, timedelta

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
