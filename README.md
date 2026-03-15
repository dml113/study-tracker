# 동아리 공부 트래커

IT 동아리 부원들의 공부 시간을 추적하고 랭킹으로 보여주는 서비스입니다.
키보드·마우스 활동을 감지해 실제로 PC 앞에 앉아 있는 시간을 측정합니다.

---

## 주요 기능

- **출근 / 퇴근** — 출근 버튼을 누른 시간부터만 측정
- **외출** — 사유 입력 후 외출 처리, 해당 시간은 측정 제외
- **실시간 랭킹** — 오늘 누가 얼마나 공부했는지 웹 대시보드에서 확인
- **관리자 페이지** — 유저 생성·삭제·비밀번호 변경, 출퇴근·외출 기록 조회
- **Windows EXE** — Python 없이 EXE 파일 하나로 실행

---

## 구조

```
study-tracker/
├── server/          # FastAPI 서버
│   ├── main.py
│   ├── models.py
│   ├── auth.py
│   ├── database.py
│   ├── routers/
│   │   ├── auth_router.py
│   │   ├── admin_router.py
│   │   └── api_router.py
│   ├── static/
│   │   ├── login.html
│   │   ├── admin.html
│   │   └── dashboard.html
│   └── requirements.txt
└── client/          # Windows 클라이언트
    ├── client.py
    ├── build.bat    # EXE 빌드 스크립트
    └── requirements.txt
```

---

## 서버 설치 (Ubuntu)

### 1. 패키지 설치

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

### 2. 프로젝트 클론

```bash
git clone https://github.com/dml113/study-tracker.git
cd study-tracker/server
```

### 3. 가상환경 및 패키지

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 서버 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. 백그라운드 자동 실행 (systemd)

```bash
sudo nano /etc/systemd/system/study-tracker.service
```

아래 내용 붙여넣기:

```ini
[Unit]
Description=Study Tracker Server
After=network.target

[Service]
User=<리눅스 유저명>
WorkingDirectory=/home/<유저명>/study-tracker/server
Environment=TZ=Asia/Seoul
ExecStart=/home/<유저명>/study-tracker/server/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable study-tracker
sudo systemctl start study-tracker
```

---

## 관리자 초기 설정

서버 첫 실행 시 기본 관리자 계정이 자동 생성됩니다.

| 항목 | 값 |
|------|-----|
| 아이디 | `admin` |
| 비밀번호 | `admin1234` |

> **첫 로그인 후 반드시 비밀번호를 변경하세요!**

---

## 웹 페이지

| URL | 설명 |
|-----|------|
| `http://서버IP:8000/` | 로그인 |
| `http://서버IP:8000/admin` | 관리자 페이지 |
| `http://서버IP:8000/dashboard` | 공부 랭킹 대시보드 |

---

## 클라이언트 사용법 (Windows)

### EXE 직접 배포받은 경우

1. `StudyTracker.exe` 실행
2. 서버 주소, 아이디, 비밀번호 입력 후 로그인
3. **출근** 버튼 클릭 → 측정 시작
4. 자리 비울 때 **외출** → 사유 입력
5. 돌아오면 **복귀**
6. 퇴근 시 **퇴근** 버튼

> 한 번 로그인하면 다음 실행부터 자동 로그인됩니다.

### EXE 직접 빌드하는 경우

**요구사항:** Python 3.11 ([다운로드](https://www.python.org/downloads/release/python-3119/))
설치 시 **"Add Python to PATH"** 반드시 체크!

```
client\build.bat
```

빌드 완료 후 `client\dist\StudyTracker.exe` 배포

---

## 동작 원리

```
[Windows 클라이언트]
  출근 클릭
    → 키보드·마우스 감지 시작
    → 60초 이상 미입력 시 비활성 처리
    → 30초마다 활동 시간 서버 전송

[서버]
  활동 시간 누적 저장 (SQLite)
  웹 대시보드에서 실시간 랭킹 표시

[외출 처리]
  외출 버튼 → 사유 입력 → 측정 중지
  복귀 버튼 → 측정 재시작
  관리자 페이지에서 외출 기록 조회 가능
```

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 서버 | Python, FastAPI, SQLite, SQLAlchemy |
| 인증 | JWT (python-jose) |
| 클라이언트 | Python, tkinter, pynput |
| 배포 | systemd, PyInstaller |

---

## 라이선스

MIT
