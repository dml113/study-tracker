# Study Tracker - Claude 개발 가이드

## 프로젝트 개요

IT 동아리 부원들의 공부 시간을 추적하는 서비스.
- **서버**: FastAPI + SQLite (Ubuntu)
- **클라이언트**: Python tkinter + pynput → PyInstaller로 Windows EXE 배포
- **인증**: JWT (python-jose)

## 디렉토리 구조

```
server/
├── main.py              # FastAPI 앱 진입점, 기본 admin 계정 자동 생성
├── models.py            # SQLAlchemy 모델 (User, ActivityLog, Attendance, Absence)
├── auth.py              # JWT 토큰 생성·검증, 비밀번호 해싱
├── database.py          # DB 엔진, 세션, init_db
├── routers/
│   ├── auth_router.py   # POST /auth/login
│   ├── api_router.py    # /api/* (heartbeat, checkin, checkout, absence, stats)
│   └── admin_router.py  # /admin/* (유저 CRUD, 출퇴근·외출 조회)
└── static/
    ├── login.html       # 로그인 페이지 (/)
    ├── admin.html       # 관리자 페이지 (/admin)
    └── dashboard.html   # 랭킹 대시보드 (/dashboard)

client/
├── client.py            # Windows 클라이언트 (tkinter UI + pynput 감지)
└── build.bat            # PyInstaller EXE 빌드 스크립트
```

## 핵심 비즈니스 로직

### 활동 측정 규칙
- 출근 버튼 클릭 후부터만 측정 시작
- 키보드·마우스 이벤트 감지, **60초** 이상 입력 없으면 비활성 처리
- 외출 중에는 측정 중지
- **30초**마다 서버에 누적 활동 시간 전송 (heartbeat)
- 퇴근 시 남은 버퍼 즉시 전송 후 종료

### DB 모델 관계
- `Attendance`: 날짜별 출근/퇴근 시각 (checkin_at, checkout_at)
- `Absence`: 외출 시작/종료 시각 + 사유 (end_at=None이면 현재 외출 중)
- `ActivityLog`: 날짜별 총 활동 시간 (active_seconds 누적)
- `User`: 계정 정보, role = "admin" | "member"

### 인증 흐름
- 모든 `/api/*`, `/admin/*` 엔드포인트는 Bearer JWT 필요
- `/admin/*`는 추가로 role="admin" 검증
- 토큰 만료: 30일

## 서버 배포 정보

- **서버**: Ubuntu 22.04, `172.16.145.81:8000`
- **서비스**: systemd `study-tracker.service`
- **타임존**: `TZ=Asia/Seoul` (서비스 파일에 설정)
- **DB**: `/home/user/study-tracker/server/study_tracker.db` (SQLite)
- **기본 관리자**: admin / admin1234

### 서버 명령어
```bash
# 서비스 재시작
sudo systemctl restart study-tracker

# 로그 확인
sudo journalctl -u study-tracker -f

# 배포 (파일 수정 후)
scp server/*.py user@172.16.145.81:~/study-tracker/server/
scp server/routers/*.py user@172.16.145.81:~/study-tracker/server/routers/
scp server/static/*.html user@172.16.145.81:~/study-tracker/server/static/
sudo systemctl restart study-tracker
```

## 개발 시 주의사항

### 서버
- `datetime.now()` 사용 (utcnow 아님) — TZ=Asia/Seoul로 서버 시간이 KST
- bcrypt는 **4.0.1** 고정 (5.x는 passlib 호환 오류)
- DB 스키마 변경 시 기존 DB 파일 삭제 후 재시작 필요 (migration 미적용)

### 클라이언트 EXE 빌드
- Python **3.11** 권장 (3.12+ PyInstaller/pynput 호환 이슈)
- pynput은 반드시 `--hidden-import` 명시 필요:
  ```
  --hidden-import=pynput.keyboard._win32
  --hidden-import=pynput.mouse._win32
  ```
- 빌드는 반드시 **Windows**에서 (Linux에서 Windows EXE 크로스컴파일 불가)

### HTML 페이지
- 순수 HTML + Vanilla JS (프레임워크 없음)
- 인증 토큰은 `localStorage`에 저장
- API 호출 시 `Authorization: Bearer <token>` 헤더 필수

## API 엔드포인트 요약

| Method | Path | 설명 |
|--------|------|------|
| POST | `/auth/login` | 로그인 → JWT 반환 |
| GET | `/api/attendance/today` | 오늘 출퇴근 상태 조회 |
| POST | `/api/checkin` | 출근 |
| POST | `/api/checkout` | 퇴근 |
| POST | `/api/absence/start` | 외출 시작 (body: reason) |
| POST | `/api/absence/end` | 복귀 |
| POST | `/api/heartbeat` | 활동 시간 전송 (body: active_seconds) |
| GET | `/api/stats` | 날짜별 랭킹 조회 |
| GET | `/admin/users` | 전체 유저 목록 |
| POST | `/admin/users` | 유저 생성 |
| PATCH | `/admin/users/{id}` | 유저 수정 |
| DELETE | `/admin/users/{id}` | 유저 삭제 |
| GET | `/admin/attendance` | 날짜별 출퇴근·외출 기록 |
