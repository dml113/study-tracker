"""
동아리 공부 트래커 클라이언트 v2
설치: pip install pynput requests
빌드: pyinstaller --onefile --windowed --name StudyTracker client.py
"""

import time
import threading
import json
import os
import tkinter as tk
from tkinter import messagebox, simpledialog
import requests
from datetime import datetime
from pynput import keyboard, mouse

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
    "running": True,
    "lock": threading.Lock(),
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
            return json.load(f)
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


# ── 백그라운드 트래킹 스레드 ──────────────────────
def on_activity(*_):
    state["last_activity"] = time.time()


def activity_counter():
    while state["running"]:
        should_count = (
            state["checked_in"]
            and not state["is_absent"]
            and time.time() - state["last_activity"] < IDLE_TIMEOUT
        )
        if should_count:
            with state["lock"]:
                state["active_buffer"] += 1.0
        time.sleep(1)


def sender():
    while state["running"]:
        time.sleep(SEND_INTERVAL)
        with state["lock"]:
            seconds = state["active_buffer"]
            state["active_buffer"] = 0.0

        if seconds > 0:
            try:
                api("post", "/api/heartbeat", json={"active_seconds": seconds})
            except Exception:
                with state["lock"]:
                    state["active_buffer"] += seconds


def start_tracking():
    kb = keyboard.Listener(on_press=on_activity)
    ms = mouse.Listener(on_move=on_activity, on_click=on_activity, on_scroll=on_activity)
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


        # 버튼 영역
        self.btn_frame = tk.Frame(self.root, bg=self.BG)
        self.btn_frame.pack(pady=10)

        self.btn_checkin = self._make_btn("출근", self.BLUE, self.checkin)
        self.btn_checkout = self._make_btn("퇴근", "#dc2626", self.checkout)
        self.btn_absence = self._make_btn("외출", self.YELLOW, self.start_absence, fg="#0f172a")
        self.btn_return = self._make_btn("복귀", self.GREEN, self.end_absence)

        tk.Button(self.root, text="웹 대시보드 열기", bg=self.BG, fg=self.MUTED,
                  relief="flat", font=("Segoe UI", 8), cursor="hand2",
                  command=lambda: __import__("webbrowser").open(state["server"])
                  ).pack(pady=(0, 8))

    def _make_btn(self, text, bg, cmd, fg="white"):
        btn = tk.Button(self.btn_frame, text=text, bg=bg, fg=fg, relief="flat",
                        font=("Segoe UI", 10, "bold"), width=8, cursor="hand2",
                        command=cmd)
        return btn

    def _refresh(self):
        # 현재 시간
        now_str = datetime.now().strftime("%H:%M:%S")
        self.lbl_time.config(text=now_str)

        # 활동 버퍼 표시
        with state["lock"]:
            buf = state["active_buffer"]
        total_mins = round(buf / 60, 1)
        self.lbl_active.config(text=f"오늘 활동: {total_mins}분 (이번 세션)")


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
        reason = simpledialog.askstring("외출", "외출 사유를 입력하세요:", parent=self.root)
        if not reason or not reason.strip():
            return
        try:
            res = api("post", "/api/absence/start", json={"reason": reason.strip()})
            data = res.json()
            if res.ok:
                state["is_absent"] = True
            else:
                messagebox.showerror("오류", data.get("detail", "외출 처리 실패"))
        except Exception:
            messagebox.showerror("오류", "서버에 연결할 수 없습니다")

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

    def on_close(self):
        if state["checked_in"]:
            if not messagebox.askyesno("종료", "아직 퇴근하지 않았습니다.\n그래도 종료하시겠습니까?"):
                return
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

        self.e_server = self._field("서버 주소", self.cfg.get("server", "http://172.16.145.81:8000"))
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


# ── 진입점 ───────────────────────────────────────
if __name__ == "__main__":
    cfg = load_config()
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
