import asyncio
import shutil
import os
import glob
import urllib.request
import json
from datetime import datetime, timedelta, date
from sqlalchemy import select, func
from database import get_session
from models import Attendance, Absence, ActivityLog, User, Group, Notice

BACKUP_DIR = "backups"
SLACK_WEBHOOK_URL = os.environ.get(
    "SLACK_WEBHOOK_URL",
    "https://hooks.slack.com/services/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",
)


def _post_slack(text: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[슬랙] 전송 실패: {e}")
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


def _fmt_min(seconds: int) -> str:
    """초 → 'X시간 Y분' 형식."""
    m = round(seconds / 60)
    h, r = divmod(m, 60)
    if h == 0:
        return f"{r}분"
    if r == 0:
        return f"{h}시간"
    return f"{h}시간 {r}분"


async def generate_weekly_report():
    """이번 주 월~토 일별 점수제 랭킹 리포트를 공지로 등록.

    매일 순공시간 1위=3점, 2위=2점, 3위=1점을 부여.
    일요일 09:00에 주간 총점 상위 3명을 공지로 발표.
    """
    today = date.today()
    # 이번 주 월요일
    this_monday = today - timedelta(days=today.weekday())
    # 월~토 (6일)
    week_days = [this_monday + timedelta(days=i) for i in range(6)]

    async for session in get_session():
        try:
            points: dict[str, int] = {}
            total_secs: dict[str, int] = {}
            medal_pts = [3, 2, 1]

            for day in week_days:
                rows = (await session.execute(
                    select(ActivityLog.username, ActivityLog.active_seconds)
                    .where(ActivityLog.date == day.isoformat())
                    .where(ActivityLog.active_seconds > 0)
                    .order_by(ActivityLog.active_seconds.desc())
                )).all()

                for i, row in enumerate(rows):
                    total_secs[row.username] = total_secs.get(row.username, 0) + (row.active_seconds or 0)
                    if i < 3:
                        points[row.username] = points.get(row.username, 0) + medal_pts[i]

            if not points:
                return

            ranked = sorted(points.items(), key=lambda x: (-x[1], -total_secs.get(x[0], 0)))
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, (username, pt) in enumerate(ranked[:10]):
                medal = medals[i] if i < 3 else f"{i+1}."
                time_str = _fmt_min(total_secs.get(username, 0))
                lines.append(f"{medal} {username}  {pt}점 ({time_str})")

            period_str = f"{this_monday.strftime('%m/%d')}~{(this_monday + timedelta(days=5)).strftime('%m/%d')}"
            body = f"이번 주({period_str}) 매일 순공시간 1위=3점·2위=2점·3위=1점 기준 집계입니다.\n\n"
            body += "\n".join(lines)

            notice = Notice(
                title=f"🏆 주간 랭킹 ({period_str})",
                body=body,
                is_active=True,
            )
            session.add(notice)
            await session.commit()
            print(f"[주간랭킹] {period_str} 공지 등록 완료")

            slack_text = f"🏆 *주간 랭킹 ({period_str})*\n매일 순공시간 1위=3점·2위=2점·3위=1점 기준\n\n" + "\n".join(lines)
            _post_slack(slack_text)
        except Exception as e:
            print(f"[주간랭킹] 실패: {e}")


async def weekly_report_scheduler():
    """매주 일요일 09:00에 주간 랭킹 공지 자동 등록."""
    while True:
        now = datetime.now()
        # 일요일 = weekday() 6
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour < 9:
            next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        else:
            if days_until_sunday == 0:
                days_until_sunday = 7
            next_run = (now + timedelta(days=days_until_sunday)).replace(hour=9, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            await generate_weekly_report()
        except Exception as e:
            print(f"[주간랭킹] 스케줄러 오류: {e}")


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
