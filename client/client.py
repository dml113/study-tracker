"""
동아리 공부 트래커 클라이언트 v2
설치: pip install pynput requests
빌드: pyinstaller --onefile --windowed --name StudyTracker client.py
"""

import time
import threading
import json
import os
import sys
import subprocess
import tempfile
import shutil
import zipfile
import tkinter as tk
from tkinter import messagebox, simpledialog
from collections import deque
import requests
from datetime import datetime
from pynput import keyboard, mouse

VERSION = "1.0.5"

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".study_tracker.json")
SEND_INTERVAL = 30
IDLE_TIMEOUT = 60

# 식사 시간 (분 단위, 평일/주말)
MEAL_TIMES = {
    "weekday": [(12*60+40, 13*60+30), (17*60+30, 18*60+30)],
    "weekend": [(13*60,    14*60),    (17*60,    18*60)],
}

state = {
    "token": None,
    "username": None,
    "server": None,
    "checked_in": False,
    "is_absent": False,
    "last_activity": time.time(),
    "active_buffer": 0.0,
    "session_total": 0.0,
    "running": True,
    "lock": threading.Lock(),
    "key_events": deque(maxlen=120),  # (timestamp, key_str)
    "is_cheating": False,
    "cheat_reason": "",
}


# ── 식사 시간 판별 ──────────────────────────────
def is_meal_time() -> bool:
    now = datetime.now()
    minutes = now.hour * 60 + now.minute
    key = "weekend" if now.weekday() >= 5 else "weekday"
    return any(start <= minutes < end for start, end in MEAL_TIMES[key])


# ── 설정 ────────────────────────────────────────
def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # 구 서버 주소 자동 마이그레이션
        if cfg.get("server") in ("http://172.16.145.81:8000", "http://traker.itnsa.cloud"):
            cfg["server"] = "http://172.16.145.16:8000"
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f)
        return cfg
    return {}


def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump({
            "server": state["server"],
            "username": state["username"],
            "token": state["token"],
        }, f)


# ── API 호출 ─────────────────────────────────────
def api(method: str, path: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    if state["token"]:
        headers["Authorization"] = f"Bearer {state['token']}"
    return getattr(requests, method)(
        f"{state['server']}{path}", headers=headers, timeout=5, **kwargs
    )


# ── 치트 감지 ─────────────────────────────────────
def detect_cheat() -> tuple[bool, str]:
    now = time.time()
    with state["lock"]:
        events = list(state["key_events"])

    recent = [(t, k) for t, k in events if now - t < 30]
    if len(recent) < 20:
        return False, ""

    keys = [k for _, k in recent]

    # Rule 1: 동일 키가 75% 이상 (키보드에 물건 올려두기)
    counts: dict[str, int] = {}
    for k in keys:
        counts[k] = counts.get(k, 0) + 1
    top_count = max(counts.values())
    if top_count / len(keys) >= 0.75:
        top_key = max(counts, key=lambda k: counts[k])
        return True, f"동일 키 반복 ({top_key})"

    # Rule 2: 입력 간격 표준편차 < 30ms (매크로)
    timestamps = [t for t, _ in recent[-25:]]
    intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    if intervals:
        avg = sum(intervals) / len(intervals)
        std_dev = (sum((x - avg) ** 2 for x in intervals) / len(intervals)) ** 0.5
        if avg < 0.5 and std_dev < 0.03:
            return True, "자동 입력 패턴 감지"

    return False, ""


# ── 백그라운드 트래킹 스레드 ──────────────────────
def on_key(key):
    now = time.time()
    state["last_activity"] = now
    with state["lock"]:
        state["key_events"].append((now, str(key)))


def on_mouse(*_):
    state["last_activity"] = time.time()


def activity_counter():
    last = time.time()
    cheat_tick = 0
    while state["running"]:
        time.sleep(1)
        now = time.time()
        elapsed = now - last
        last = now

        # 10초마다 치트 감지
        cheat_tick += 1
        if cheat_tick >= 10:
            cheat_tick = 0
            is_cheat, reason = detect_cheat()
            with state["lock"]:
                prev = state["is_cheating"]
                state["is_cheating"] = is_cheat
                if is_cheat:
                    state["cheat_reason"] = reason
                elif prev:
                    state["cheat_reason"] = ""
            if is_cheat and not prev:
                try:
                    api("post", "/api/cheat-report", json={"reason": reason})
                except Exception:
                    pass

        with state["lock"]:
            cheating = state["is_cheating"]

        should_count = (
            state["checked_in"]
            and not state["is_absent"]
            and not is_meal_time()
            and not cheating
            and now - state["last_activity"] < IDLE_TIMEOUT
        )
        if should_count:
            with state["lock"]:
                state["active_buffer"] += elapsed


def sender():
    while state["running"]:
        time.sleep(SEND_INTERVAL)
        with state["lock"]:
            seconds = state["active_buffer"]
            state["active_buffer"] = 0.0

        if seconds > 0:
            try:
                api("post", "/api/heartbeat", json={"active_seconds": seconds})
                with state["lock"]:
                    state["session_total"] += seconds
            except Exception:
                with state["lock"]:
                    state["active_buffer"] += seconds


def start_tracking():
    kb = keyboard.Listener(on_press=on_key)
    ms = mouse.Listener(on_move=on_mouse, on_click=on_mouse, on_scroll=on_mouse)
    kb.daemon = True
    ms.daemon = True
    kb.start()
    ms.start()
    threading.Thread(target=activity_counter, daemon=True).start()
    threading.Thread(target=sender, daemon=True).start()


# ── 메인 창 ──────────────────────────────────────
class MainWindow:
    BG = "#0f172a"
    CARD = "#1e293b"
    BORDER = "#334155"
    BLUE = "#3b82f6"
    GREEN = "#22c55e"
    YELLOW = "#f59e0b"
    GRAY = "#475569"
    TEXT = "#e2e8f0"
    MUTED = "#94a3b8"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("공부 트래커")
        self.root.geometry("320x320")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        # 헤더
        hdr = tk.Frame(self.root, bg=self.CARD, pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="공부 트래커", bg=self.CARD, fg=self.BLUE,
                 font=("Segoe UI", 13, "bold")).pack()
        tk.Label(hdr, text=f"{state['username']}님", bg=self.CARD, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack()

        # 상태 영역
        body = tk.Frame(self.root, bg=self.BG, pady=18)
        body.pack(fill="both", expand=True)

        self.lbl_status = tk.Label(body, text="", bg=self.BG, fg=self.TEXT,
                                   font=("Segoe UI", 12, "bold"))
        self.lbl_status.pack()

        self.lbl_time = tk.Label(body, text="", bg=self.BG, fg=self.MUTED,
                                 font=("Segoe UI", 9))
        self.lbl_time.pack(pady=(4, 0))

        self.lbl_active = tk.Label(body, text="", bg=self.BG, fg=self.BLUE,
                                   font=("Segoe UI", 9))
        self.lbl_active.pack(pady=(2, 0))

        self.lbl_cheat = tk.Label(body, text="", bg=self.BG, fg="#ef4444",
                                  font=("Segoe UI", 8, "bold"))
        self.lbl_cheat.pack(pady=(2, 0))


        # 버튼 영역
        self.btn_frame = tk.Frame(self.root, bg=self.BG)
        self.btn_frame.pack(pady=10)

        self.btn_checkin = self._make_btn("출근", self.BLUE, self.checkin)
        self.btn_checkout = self._make_btn("퇴근", "#dc2626", self.checkout)
        self.btn_absence = self._make_btn("외출", self.YELLOW, self.start_absence, fg="#0f172a")
        self.btn_return = self._make_btn("복귀", self.GREEN, self.end_absence)

        foot = tk.Frame(self.root, bg=self.BG)
        foot.pack(pady=(0, 8))
        tk.Button(foot, text="웹 대시보드 열기", bg=self.BG, fg=self.MUTED,
                  relief="flat", font=("Segoe UI", 8), cursor="hand2",
                  command=lambda: __import__("webbrowser").open(state["server"])
                  ).pack(side="left", padx=8)
        tk.Button(foot, text="비밀번호 변경", bg=self.BG, fg=self.MUTED,
                  relief="flat", font=("Segoe UI", 8), cursor="hand2",
                  command=self.change_password
                  ).pack(side="left", padx=8)

    def _make_btn(self, text, bg, cmd, fg="white"):
        btn = tk.Button(self.btn_frame, text=text, bg=bg, fg=fg, relief="flat",
                        font=("Segoe UI", 10, "bold"), width=8, cursor="hand2",
                        command=cmd)
        return btn

    def _refresh(self):
        # 현재 시간
        now_str = datetime.now().strftime("%H:%M:%S")
        self.lbl_time.config(text=now_str)

        # 활동 시간 표시 (전송 완료분 + 현재 버퍼)
        with state["lock"]:
            buf = state["active_buffer"]
            total = state["session_total"] + buf
            cheating = state["is_cheating"]
            cheat_reason = state["cheat_reason"]
        total_mins = round(total / 60, 1)
        self.lbl_active.config(text=f"오늘 활동: {total_mins}분 (이번 세션)")
        if cheating:
            self.lbl_cheat.config(text=f"⚠ 비정상 입력 감지 — 측정 중단 ({cheat_reason})")
        else:
            self.lbl_cheat.config(text="")


        # 상태 + 버튼 배치
        for w in self.btn_frame.winfo_children():
            w.pack_forget()

        if not state["checked_in"]:
            self.lbl_status.config(text="○  출근 전", fg=self.GRAY)
            self.btn_checkin.pack(pady=4)
        elif state["is_absent"]:
            self.lbl_status.config(text="◐  외출 중", fg=self.YELLOW)
            self.btn_return.pack(pady=4)
        else:
            self.lbl_status.config(text="●  출근 중", fg=self.GREEN)
            self.btn_absence.pack(side="left", padx=6)
            self.btn_checkout.pack(side="left", padx=6)

        self.root.after(1000, self._refresh)

    # ── 출근 ──
    def checkin(self):
        try:
            res = api("post", "/api/checkin")
            data = res.json()
            if res.ok:
                state["checked_in"] = True
                state["is_absent"] = False
            else:
                messagebox.showerror("오류", data.get("detail", "출근 실패"))
        except Exception:
            messagebox.showerror("오류", "서버에 연결할 수 없습니다")

    # ── 퇴근 ──
    def checkout(self):
        if not messagebox.askyesno("퇴근", "퇴근하시겠습니까?"):
            return
        try:
            # 남은 버퍼 먼저 전송
            with state["lock"]:
                seconds = state["active_buffer"]
                state["active_buffer"] = 0.0
            if seconds > 0:
                api("post", "/api/heartbeat", json={"active_seconds": seconds})

            res = api("post", "/api/checkout")
            data = res.json()
            if res.ok:
                state["checked_in"] = False
                state["is_absent"] = False
            else:
                messagebox.showerror("오류", data.get("detail", "퇴근 실패"))
        except Exception:
            messagebox.showerror("오류", "서버에 연결할 수 없습니다")

    # ── 외출 시작 ──
    def start_absence(self):
        PRESET_REASONS = ["점심시간", "저녁시간", "화장실", "휴식", "기타"]

        dialog = tk.Toplevel(self.root)
        dialog.title("외출")
        dialog.geometry("260x260")
        dialog.resizable(False, False)
        dialog.configure(bg=self.BG)
        dialog.grab_set()

        tk.Label(dialog, text="외출 사유", bg=self.BG, fg=self.BLUE,
                 font=("Segoe UI", 11, "bold")).pack(pady=(16, 10))

        selected = tk.StringVar(value=PRESET_REASONS[0])
        for r in PRESET_REASONS:
            tk.Radiobutton(dialog, text=r, variable=selected, value=r,
                           bg=self.BG, fg=self.TEXT, selectcolor=self.CARD,
                           activebackground=self.BG, activeforeground=self.TEXT,
                           font=("Segoe UI", 9)).pack(anchor="w", padx=24)

        e_other = tk.Entry(dialog, bg=self.CARD, fg=self.TEXT, insertbackground="white",
                           relief="flat", font=("Segoe UI", 9))
        e_other.pack(fill="x", padx=24, pady=(4, 0), ipady=5)

        def on_radio(*_):
            e_other.config(state="normal" if selected.get() == "기타" else "disabled")
            if selected.get() != "기타":
                e_other.delete(0, "end")

        selected.trace_add("write", on_radio)
        e_other.config(state="disabled")

        lbl_err = tk.Label(dialog, text="", bg=self.BG, fg="#f87171", font=("Segoe UI", 8))
        lbl_err.pack()

        def submit():
            r = selected.get()
            if r == "기타":
                r = e_other.get().strip()
                if not r:
                    lbl_err.config(text="기타 사유를 입력하세요")
                    return
            try:
                res = api("post", "/api/absence/start", json={"reason": r})
                data = res.json()
                if res.ok:
                    state["is_absent"] = True
                    dialog.destroy()
                else:
                    lbl_err.config(text=data.get("detail", "외출 처리 실패"))
            except Exception:
                lbl_err.config(text="서버에 연결할 수 없습니다")

        tk.Button(dialog, text="외출", bg=self.YELLOW, fg="#0f172a", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=submit).pack(fill="x", padx=24, pady=(4, 0), ipady=6)
        dialog.bind("<Return>", lambda _: submit())

    # ── 복귀 ──
    def end_absence(self):
        try:
            res = api("post", "/api/absence/end")
            data = res.json()
            if res.ok:
                state["is_absent"] = False
            else:
                messagebox.showerror("오류", data.get("detail", "복귀 처리 실패"))
        except Exception:
            messagebox.showerror("오류", "서버에 연결할 수 없습니다")

    def change_password(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("비밀번호 변경")
        dialog.geometry("280x220")
        dialog.resizable(False, False)
        dialog.configure(bg=self.BG)
        dialog.grab_set()

        tk.Label(dialog, text="비밀번호 변경", bg=self.BG, fg=self.BLUE,
                 font=("Segoe UI", 11, "bold")).pack(pady=(18, 12))

        def field(label, show=""):
            f = tk.Frame(dialog, bg=self.BG)
            f.pack(fill="x", padx=20, pady=(0, 8))
            tk.Label(f, text=label, bg=self.BG, fg=self.MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w")
            e = tk.Entry(f, bg=self.CARD, fg=self.TEXT, insertbackground="white",
                         relief="flat", font=("Segoe UI", 10), show=show)
            e.pack(fill="x", ipady=5)
            return e

        e_cur = field("현재 비밀번호", "●")
        e_new = field("새 비밀번호", "●")

        lbl_err = tk.Label(dialog, text="", bg=self.BG, fg="#f87171", font=("Segoe UI", 8))
        lbl_err.pack()

        def submit():
            cur = e_cur.get()
            new = e_new.get()
            if not cur or not new:
                lbl_err.config(text="모든 항목을 입력하세요")
                return
            if len(new) < 4:
                lbl_err.config(text="비밀번호는 4자 이상이어야 합니다")
                return
            try:
                res = api("post", "/api/change-password",
                          json={"current_password": cur, "new_password": new})
                data = res.json()
                if res.ok:
                    dialog.destroy()
                    messagebox.showinfo("완료", "비밀번호가 변경되었습니다")
                else:
                    lbl_err.config(text=data.get("detail", "변경 실패"))
            except Exception:
                lbl_err.config(text="서버에 연결할 수 없습니다")

        tk.Button(dialog, text="변경", bg=self.BLUE, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=submit).pack(fill="x", padx=20, pady=(6, 0), ipady=6)
        dialog.bind("<Return>", lambda _: submit())

    def on_close(self):
        if state["checked_in"]:
            if not messagebox.askyesno("종료", "아직 퇴근하지 않았습니다.\n그래도 종료하시겠습니까?"):
                return
            # 남은 버퍼 전송
            with state["lock"]:
                seconds = state["active_buffer"]
                state["active_buffer"] = 0.0
            if seconds > 0:
                try:
                    api("post", "/api/heartbeat", json={"active_seconds": seconds})
                except Exception:
                    pass
        state["running"] = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── 로그인 창 ─────────────────────────────────────
class LoginWindow:
    BG = "#0f172a"
    CARD = "#1e293b"
    BORDER = "#334155"
    BLUE = "#3b82f6"

    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title("공부 트래커 로그인")
        self.root.geometry("320x290")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self._build()

    def _build(self):
        tk.Label(self.root, text="공부 트래커", bg=self.BG, fg=self.BLUE,
                 font=("Segoe UI", 14, "bold")).pack(pady=(24, 2))
        tk.Label(self.root, text="동아리 공부 시간 모니터링", bg=self.BG, fg="#475569",
                 font=("Segoe UI", 9)).pack(pady=(0, 20))

        self.e_server = self._field("서버 주소", self.cfg.get("server", "http://172.16.145.16:8000"))
        self.e_user = self._field("아이디", self.cfg.get("username", ""))
        self.e_pw = self._field("비밀번호", "", show="●")

        self.lbl_err = tk.Label(self.root, text="", bg=self.BG, fg="#f87171",
                                font=("Segoe UI", 8))
        self.lbl_err.pack()

        tk.Button(self.root, text="로그인", bg=self.BLUE, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=self.do_login).pack(fill="x", padx=24, pady=(10, 0), ipady=8)

        self.root.bind("<Return>", lambda _: self.do_login())

    def _field(self, label, default="", show=""):
        frame = tk.Frame(self.root, bg=self.BG)
        frame.pack(fill="x", padx=24, pady=(0, 8))
        tk.Label(frame, text=label, bg=self.BG, fg="#94a3b8",
                 font=("Segoe UI", 8)).pack(anchor="w")
        e = tk.Entry(frame, bg=self.CARD, fg="#e2e8f0", insertbackground="white",
                     relief="flat", font=("Segoe UI", 10), show=show)
        e.pack(fill="x", ipady=6)
        e.insert(0, default)
        return e

    def do_login(self):
        server = self.e_server.get().strip().rstrip("/")
        username = self.e_user.get().strip()
        password = self.e_pw.get()
        if not all([server, username, password]):
            self.lbl_err.config(text="모든 항목을 입력하세요")
            return
        try:
            res = requests.post(f"{server}/auth/login",
                                data={"username": username, "password": password},
                                timeout=5)
            data = res.json()
            if not res.ok:
                self.lbl_err.config(text=data.get("detail", "로그인 실패"))
                return

            state.update(token=data["access_token"], username=data["username"], server=server)
            save_config()
            self.root.destroy()
            self._load_attendance_state()
            start_tracking()
            MainWindow().run()

        except requests.exceptions.ConnectionError:
            self.lbl_err.config(text="서버에 연결할 수 없습니다")
        except Exception as e:
            self.lbl_err.config(text=f"오류: {e}")

    def _load_attendance_state(self):
        try:
            res = api("get", "/api/attendance/today")
            if res.ok:
                data = res.json()
                state["checked_in"] = data["checked_in"]
                state["is_absent"] = data["is_absent"]
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ── 자동 업데이트 ─────────────────────────────────
def check_for_update(server: str):
    try:
        res = requests.get(f"{server}/client/version", timeout=5)
        if not res.ok:
            return
        latest = res.json().get("version", "")
        if not latest or latest == VERSION:
            return

        if not messagebox.askyesno(
            "업데이트",
            f"새 버전 {latest}이(가) 있습니다.\n지금 업데이트하시겠습니까?",
        ):
            return

        # 다운로드 (zip)
        dl = requests.get(f"{server}/client/download", timeout=120, stream=True)
        if not dl.ok:
            messagebox.showerror("오류", "다운로드 실패")
            return

        tmp_zip = os.path.join(tempfile.gettempdir(), "st_update.zip")
        with open(tmp_zip, "wb") as f:
            for chunk in dl.iter_content(chunk_size=65536):
                f.write(chunk)

        # zip 압축 해제
        tmp_dir = os.path.join(tempfile.gettempdir(), "st_update_extract")
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        with zipfile.ZipFile(tmp_zip, "r") as z:
            z.extractall(tmp_dir)
        os.remove(tmp_zip)

        current = sys.executable
        app_dir = os.path.dirname(current)

        # 배치 스크립트로 폴더 전체 교체 후 재시작
        bat = os.path.join(tempfile.gettempdir(), "st_update.bat")
        with open(bat, "w") as f:
            f.write(
                f"@echo off\n"
                f"timeout /t 2 /nobreak > nul\n"
                f"xcopy /E /Y /I \"{tmp_dir}\\*\" \"{app_dir}\\\"\n"
                f"start \"\" \"{current}\"\n"
                f"rmdir /s /q \"{tmp_dir}\"\n"
                f"del \"%~f0\"\n"
            )
        subprocess.Popen(bat, shell=True)
        sys.exit()

    except Exception:
        pass


# ── 진입점 ───────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
    if cfg.get("server"):
        check_for_update(cfg["server"])
    # 저장된 토큰으로 자동 로그인 시도
    if cfg.get("token") and cfg.get("server") and cfg.get("username"):
        state.update(token=cfg["token"], username=cfg["username"], server=cfg["server"])
        try:
            res = api("get", "/api/attendance/today")
            if res.ok:
                data = res.json()
                state["checked_in"] = data["checked_in"]
                state["is_absent"] = data["is_absent"]
                start_tracking()
                MainWindow().run()
                exit()
        except Exception:
            pass

    LoginWindow().run()
