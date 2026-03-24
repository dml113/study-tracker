# 동아리 공부 트래커

IT 동아리 부원들의 공부 시간을 추적하고 랭킹으로 보여주는 서비스.
키보드·마우스 활동을 감지해 실제로 PC 앞에 앉아 있는 시간을 측정합니다.

---

## 주요 기능

- **출근 / 퇴근** — 출근 버튼을 누른 시간부터만 측정
- **외출** — 사유 입력 후 외출 처리, 해당 시간은 측정 제외
- **식사 시간 자동 제외** — 평일/주말 점심·저녁 시간 자동 제외
- **치트 감지** — 키보드에 물건 올리기·매크로 자동 감지 및 측정 중단
- **실시간 랭킹** — 오늘 누가 얼마나 공부했는지 웹 대시보드에서 확인
- **그룹 관리** — C.C, IT, C.S 등 소그룹별 관리자 지정
- **클라이언트 자동 업데이트** — 서버에 새 EXE 올리면 다음 실행 시 자동 업데이트
- **GitHub Actions 자동 빌드** — 버전 파일만 수정하면 EXE 자동 빌드 및 Release 업로드
- **Windows EXE** — Python 없이 EXE 파일 하나로 실행

---

## 구조

```
study-tracker/
├── server/          # FastAPI 서버
│   ├── main.py              # 앱 진입점, 클라이언트 배포 엔드포인트
│   ├── models.py            # DB 모델 (User, Group, ActivityLog, Attendance, Absence, CheatLog)
│   ├── auth.py              # JWT, 비밀번호 해싱, 권한 검증
│   ├── database.py          # DB 엔진, 세션
│   ├── routers/
│   │   ├── auth_router.py
│   │   ├── admin_router.py
│   │   └── api_router.py
│   ├── static/
│   │   ├── login.html       # 로그인 + 클라이언트 다운로드
│   │   ├── admin.html       # 관리자 페이지
│   │   └── dashboard.html   # 랭킹 대시보드
│   └── client_dist/         # 배포용 EXE + version.txt (자동 생성)
├── client/
│   ├── client.py            # Windows 클라이언트
│   ├── build.bat            # EXE 빌드 스크립트 (로컬용)
│   └── VERSION              # 버전 파일 (변경 시 자동 빌드 트리거)
└── .github/workflows/
    └── build-client.yml     # GitHub Actions 자동 빌드
```

---

## 역할 체계

| 역할 | 설명 |
|------|------|
| `superadmin` | 슈퍼 어드민 (1개). 그룹 생성/삭제, 모든 유저 관리, 클라이언트 배포 |
| `group_admin` | 그룹 어드민. 자기 그룹 멤버만 생성/수정/삭제/조회 |
| `member` | 일반 부원 |

---

## 서버 설치 (Ubuntu)

### 1. 패키지 설치

```bash
sudo apt update && sudo apt install python3 python3-pip python3-venv
```

### 2. 프로젝트 클론

```bash
git clone https://github.com/dml113/study-tracker.git
cd study-tracker
```

### 3. 가상환경 및 패키지

```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy aiosqlite "python-jose[cryptography]" "passlib[bcrypt]" "bcrypt==4.0.1" python-multipart
```

### 4. systemd 서비스 등록

```ini
[Unit]
Description=Study Tracker Server
After=network.target

[Service]
User=<리눅스 유저명>
WorkingDirectory=/home/<유저명>/study-tracker
Environment=TZ=Asia/Seoul
ExecStart=/home/<유저명>/study-tracker/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now study-tracker
```

> **주의**: `WorkingDirectory`는 `study-tracker/` 루트이며, `server/` 하위가 아닙니다.

### 초기 관리자 계정

서버 첫 실행 시 자동 생성됩니다.

| 아이디 | 비밀번호 |
|--------|---------|
| `admin` | `admin1234` |

> 첫 로그인 후 반드시 비밀번호를 변경하세요.

---

## 클라이언트 배포

### GitHub Actions 자동 빌드 (권장)

1. `client/VERSION` 파일을 새 버전으로 수정 (예: `1.0.1`)
2. commit & push → GitHub Actions가 자동으로 Windows EXE 빌드 (약 1~2분)
3. 빌드된 EXE가 GitHub Release에 자동 업로드 (태그: `v{버전}`)
4. 관리자 페이지(`/admin`) → **클라이언트 배포** 탭 → **"GitHub에서 동기화"** 클릭
5. 부원들 다음 실행 시 자동 업데이트 알림

> 서버가 사설IP라 GitHub Actions에서 직접 업로드할 수 없어, 관리자가 동기화 버튼을 눌러 서버로 가져오는 방식입니다.

### 수동 빌드 (보조 수단)

로컬 Windows에서 직접 빌드 후 관리자 페이지에서 업로드할 수도 있습니다.

1. Windows에서 Python 3.11 설치 (PATH 추가 체크)
2. `pip install pynput requests pyinstaller`
3. `client\build.bat` 실행 → `client\dist\StudyTracker.exe` 생성
4. 관리자 페이지(`/admin`) → **클라이언트 배포** 탭 → 버전 번호 + EXE 파일 업로드

### 빌드 명령어 (로컬)

```bat
pyinstaller --onefile --windowed --name StudyTracker ^
  --hidden-import=pynput.keyboard._win32 ^
  --hidden-import=pynput.mouse._win32 ^
  client.py
```

---

## 웹 페이지

| URL | 설명 |
|-----|------|
| `/` | 로그인 + 클라이언트 다운로드 |
| `/admin` | 관리자 페이지 |
| `/dashboard` | 공부 랭킹 대시보드 |

### 관리자 페이지 탭

| 탭 | 설명 |
|----|------|
| 유저 관리 | 유저 생성/삭제/비활성화, 그룹 배정, 역할 변경 |
| 공부 통계 | 날짜별 활동 시간 랭킹 |
| 출퇴근 기록 | 날짜별 출근·퇴근·외출 기록 |
| 치트 감지 | 날짜별 비정상 입력 감지 로그 |
| 그룹 관리 | 그룹 생성/삭제 (슈퍼 어드민 전용) |
| 클라이언트 배포 | EXE 업로드, GitHub 동기화, 버전 관리 (슈퍼 어드민 전용) |

---

## 클라이언트 사용법 (Windows)

1. `StudyTracker.exe` 실행
2. 서버 주소, 아이디, 비밀번호 입력 후 로그인
3. **출근** 버튼 클릭 → 측정 시작
4. 자리 비울 때 **외출** → 사유 입력
5. 돌아오면 **복귀**
6. 퇴근 시 **퇴근** 버튼

한 번 로그인하면 다음 실행부터 자동 로그인됩니다.

---

## 치트 감지

| 패턴 | 감지 조건 |
|------|---------|
| 키보드에 물건 올리기 | 30초 내 같은 키가 75% 이상 |
| 매크로 | 입력 간격 평균 <500ms + 표준편차 <30ms |

감지 시 활동 측정 즉시 중단 + 서버 자동 신고. 정상 입력 재개 시 자동 해제됩니다.

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 서버 | Python, FastAPI, SQLite, SQLAlchemy (async) |
| 인증 | JWT (python-jose), bcrypt 4.0.1 |
| 클라이언트 | Python 3.11, tkinter, pynput |
| 배포 | systemd, PyInstaller, GitHub Actions |

---

## 라이선스

MIT
