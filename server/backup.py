import asyncio
import shutil
import os
import glob
import urllib.request
import json
from datetime import datetime, timedelta, date
from sqlalchemy import select, func
from database import get_session
from models import Attendance, Absence, ActivityLog, User, Group, Notice, UserPoint, PointLog

BACKUP_DIR = "backups"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def _post_slack(text: str, username: str = "Study-Tracker", icon_emoji: str = ":books:"):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        data = json.dumps({"text": text, "username": username, "icon_emoji": icon_emoji}).encode("utf-8")
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
            days_studied: dict[str, int] = {}
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
                    days_studied[row.username] = days_studied.get(row.username, 0) + 1
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
                days = days_studied.get(username, 0)
                lines.append(f"{medal} {username}  {pt}점 ({time_str}, {days}일 출석)")

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

            slack_text = f"🏆 이번 주 랭킹이 나왔어요! ({period_str})\n매일 1위=3점·2위=2점·3위=1점 기준이에요 🎯\n\n" + "\n".join(lines) + "\n\n다음 주도 열심히 해봐요 💪"
            _post_slack(slack_text, username="Study-Ranking", icon_emoji=":trophy:")
            print(f"[주간랭킹] {period_str} 슬랙 전송 완료")

            # 랭킹 포인트 보너스 (1위=30, 2위=20, 3위=10)
            ranking_bonuses = [30, 20, 10]
            for i, (username, _pt) in enumerate(ranked[:3]):
                bonus = ranking_bonuses[i]
                up_result = await session.execute(select(UserPoint).where(UserPoint.username == username))
                user_point = up_result.scalar_one_or_none()
                if not user_point:
                    user_point = UserPoint(username=username)
                    session.add(user_point)
                user_point.points += bonus
                session.add(PointLog(username=username, amount=bonus, reason="ranking"))
            await session.commit()

            # 그룹별 슬랙
            user_rows = (await session.execute(select(User.username, User.group_id))).all()
            group_rows = (await session.execute(select(Group))).scalars().all()
            user_group = {r.username: r.group_id for r in user_rows}
            group_names = {g.id: g.name for g in group_rows}
            for gid, gname in group_names.items():
                g_ranked = [(u, p) for u, p in ranked if user_group.get(u) == gid]
                if len(g_ranked) < 2:
                    continue
                g_lines = []
                for i, (username, pt) in enumerate(g_ranked[:5]):
                    medal = medals[i] if i < 3 else f"{i+1}."
                    time_str = _fmt_min(total_secs.get(username, 0))
                    days = days_studied.get(username, 0)
                    g_lines.append(f"{medal} {username}  {pt}점 ({time_str}, {days}일)")
                _post_slack(f"🏆 [{gname}] 이번 주 랭킹이에요! ({period_str}) 🎉\n\n" + "\n".join(g_lines) + "\n\n다음 주도 화이팅! 💪", username="Study-Ranking", icon_emoji=":trophy:")
        except Exception as e:
            print(f"[주간랭킹] 실패: {e}")


async def generate_daily_report():
    """오늘 하루 공부 랭킹을 슬랙으로 전송."""
    today = date.today() - timedelta(days=1)  # 새벽 1시이므로 어제 날짜
    today_str = today.isoformat()

    async for session in get_session():
        try:
            rows = (await session.execute(
                select(ActivityLog.username, ActivityLog.active_seconds)
                .where(ActivityLog.date == today_str)
                .where(ActivityLog.active_seconds > 0)
                .order_by(ActivityLog.active_seconds.desc())
            )).all()

            if not rows:
                return

            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, row in enumerate(rows):
                medal = medals[i] if i < 3 else f"{i+1}."
                lines.append(f"{medal} {row.username}  {_fmt_min(row.active_seconds)}")

            date_str = today.strftime("%m/%d (%a)").replace(
                "Mon", "월").replace("Tue", "화").replace("Wed", "수").replace(
                "Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일")
            slack_text = f"📅 {date_str} 공부 랭킹이에요! 오늘도 수고했어요 😊\n\n" + "\n".join(lines) + "\n\n내일도 파이팅! 🔥"
            _post_slack(slack_text, username="Study-Ranking", icon_emoji=":bar_chart:")
            print(f"[일간랭킹] {today_str} 슬랙 전송 완료")

            # 그룹별 슬랙
            user_rows = (await session.execute(select(User.username, User.group_id))).all()
            group_rows = (await session.execute(select(Group))).scalars().all()
            user_group = {r.username: r.group_id for r in user_rows}
            group_names = {g.id: g.name for g in group_rows}
            row_map = {r.username: r.active_seconds for r in rows}
            for gid, gname in group_names.items():
                g_rows = [(u, s) for u, s in row_map.items() if user_group.get(u) == gid]
                g_rows.sort(key=lambda x: -x[1])
                if len(g_rows) < 2:
                    continue
                g_lines = []
                for i, (uname, secs) in enumerate(g_rows[:5]):
                    medal = medals[i] if i < 3 else f"{i+1}."
                    g_lines.append(f"{medal} {uname}  {_fmt_min(secs)}")
                _post_slack(f"📅 [{gname}] {date_str} 랭킹이에요! 모두 수고했어요 😊\n\n" + "\n".join(g_lines) + "\n\n내일도 파이팅! 🔥", username="Study-Ranking", icon_emoji=":bar_chart:")
        except Exception as e:
            print(f"[일간랭킹] 실패: {e}")


async def daily_report_scheduler():
    """매일 01:10에 전날 공부 랭킹을 슬랙으로 전송 (자동퇴근 00:50 이후 여유)."""
    while True:
        now = datetime.now()
        target = now.replace(hour=1, minute=10, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await generate_daily_report()
        except Exception as e:
            print(f"[일간랭킹] 스케줄러 오류: {e}")


async def generate_morning_checkin():
    """오전 10시 현재 출근 중인 인원 슬랙 전송."""
    today = date.today().isoformat()
    async for session in get_session():
        try:
            att_result = await session.execute(
                select(Attendance).where(
                    Attendance.date == today,
                    Attendance.checkin_at != None,
                    Attendance.checkout_at == None,
                ).order_by(Attendance.checkin_at)
            )
            attendances = att_result.scalars().all()
            if not attendances:
                _post_slack("☀️ 좋은 아침이에요! 아직 아무도 출근하지 않았어요 😴\n먼저 시작하는 사람이 오늘의 주인공! 🌟", username="Study-Morning", icon_emoji=":sunny:")
                return
            lines = [f"• {a.username} ({a.checkin_at.strftime('%H:%M')} 출근)" for a in attendances]
            _post_slack(f"☀️ 좋은 아침이에요! 지금 {len(attendances)}명이 열심히 공부 중이에요 📚\n\n" + "\n".join(lines) + "\n\n오늘도 화이팅! 🌟", username="Study-Morning", icon_emoji=":sunny:")
            print(f"[아침출석] 슬랙 전송 완료 ({len(attendances)}명)")
        except Exception as e:
            print(f"[아침출석] 실패: {e}")


async def morning_checkin_scheduler():
    """매일 10:00에 출석 현황을 슬랙으로 전송."""
    while True:
        now = datetime.now()
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await generate_morning_checkin()
        except Exception as e:
            print(f"[아침출석] 스케줄러 오류: {e}")


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
