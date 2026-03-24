# Study Tracker - Claude 개발 가이드

## 프로젝트 개요

IT 동아리 부원들의 공부 시간을 추적하는 서비스.
- **서버**: FastAPI + SQLite (Ubuntu)
- **클라이언트**: Python tkinter + pynput → PyInstaller로 Windows EXE 배포
- **인증**: JWT (python-jose)

## 디렉토리 구조

```
server/
├── main.py              # FastAPI 앱 진입점, 기본 superadmin 계정 자동 생성, 클라이언트 배포 엔드포인트
├── models.py            # SQLAlchemy 모델 (User, Group, ActivityLog, Attendance, Absence, CheatLog)
├── auth.py              # JWT 토큰 생성·검증, 비밀번호 해싱, get_current_admin/get_current_superadmin
├── database.py          # DB 엔진, 세션, init_db
├── routers/
│   ├── auth_router.py   # POST /auth/login (role, group_id JWT에 포함)
│   ├── api_router.py    # /api/* (heartbeat, checkin, checkout, absence, stats, cheat-report)
│   └── admin_router.py  # /admin/* (유저 CRUD, 그룹 CRUD, 출퇴근·외출 조회, 치트 조회)
├── static/
│   ├── login.html       # 로그인 페이지 (/) + 클라이언트 다운로드 버튼
│   ├── admin.html       # 관리자 페이지 (/admin)
│   └── dashboard.html   # 랭킹 대시보드 (/dashboard)
└── client_dist/         # 배포용 EXE + version.txt (서버 시작 시 자동 생성)

client/
├── client.py            # Windows 클라이언트 (tkinter UI + pynput 감지 + 자동 업데이트 + 치트 감지)
└── build.bat            # PyInstaller EXE 빌드 스크립트
```

## 역할(Role) 체계

| Role | 권한 |
|------|------|
| `superadmin` | 모든 것 (그룹 생성/삭제, 모든 유저 관리, 클라이언트 배포) |
| `group_admin` | 자기 그룹의 member만 생성/수정/삭제/조회 |
| `member` | 일반 유저 (출근/퇴근/외출/heartbeat) |

- superadmin 계정은 하나만 존재 (auto-created: admin / admin1234)
- superadmin 계정은 삭제·role 변경 불가

## 핵심 비즈니스 로직

### 활동 측정 규칙
- 출근 버튼 클릭 후부터만 측정 시작
- 키보드·마우스 이벤트 감지, **60초** 이상 입력 없으면 비활성 처리
- 외출 중·식사 시간에는 측정 중지
- **30초**마다 서버에 누적 활동 시간 전송 (heartbeat)
- 퇴근 시 / 앱 강제 종료 시 남은 버퍼 즉시 전송
- `time.sleep(1)` drift 방지: 실제 경과 시간(`elapsed`) 기반 누적

### 식사 시간 자동 제외
```python
MEAL_TIMES = {
    "weekday": [(12*60+40, 13*60+30), (17*60+30, 18*60+30)],
    "weekend": [(13*60, 14*60), (17*60, 18*60)],
}
```

### 치트 감지 (client.py)
10초마다 최근 30초 키 이벤트 분석:
1. **동일 키 75% 이상** → 키보드에 물건 올려두기로 판단
2. **입력 간격 평균 <500ms + 표준편차 <30ms** → 매크로로 판단

감지 시: 활동 시간 카운트 중단 + UI 경고 표시 + 서버에 신고(`POST /api/cheat-report`)

### 자동 업데이트 (client.py)
앱 시작 시 `GET /client/version` 체크 → 버전 다르면 다운로드 후 .bat 스크립트로 자기 자신 교체

### DB 모델 관계
- `Group`: 그룹 (C.C, IT, C.S 등)
- `User`: 계정 정보, role, group_id(FK)
- `Attendance`: 날짜별 출근/퇴근 시각
- `Absence`: 외출 시작/종료 시각 + 사유 (end_at=None이면 외출 중)
- `ActivityLog`: 날짜별 총 활동 시간 (active_seconds 누적)
- `CheatLog`: 치트 감지 로그 (username, date, detected_at, reason)

### 인증 흐름
- 모든 `/api/*`, `/admin/*` 엔드포인트는 Bearer JWT 필요
- `/admin/*`: role이 superadmin 또는 group_admin이어야 함
- `/admin/groups`, `/admin/client/upload`: superadmin 전용
- JWT payload: `{sub, role, group_id}`
- 토큰 만료: 30일

## 서버 배포 정보

- **서버**: Ubuntu 22.04, `172.16.145.81:8000`
- **서비스**: systemd `study-tracker.service`
- **WorkingDirectory**: `/home/user/study-tracker` (server/ 하위가 아님!)
- **타임존**: `TZ=Asia/Seoul` (서비스 파일에 설정)
- **DB**: `/home/user/study-tracker/study_tracker.db` (SQLite)
- **클라이언트 배포 디렉토리**: `/home/user/study-tracker/client_dist/`
- **기본 관리자**: admin / admin1234

### 배포 명령어
```bash
# 올바른 배포 경로 (WorkingDirectory = ~/study-tracker, server/ 아님)
scp server/*.py user@172.16.145.81:~/study-tracker/
scp server/routers/*.py user@172.16.145.81:~/study-tracker/routers/
scp server/static/*.html user@172.16.145.81:~/study-tracker/static/

# 캐시 제거 후 재시작 (중요: __pycache__ 남아있으면 구버전 실행됨)
ssh user@172.16.145.81 'find ~/study-tracker -name "__pycache__" -not -path "*/venv/*" -exec rm -rf {} + 2>/dev/null'
sudo systemctl restart study-tracker

# 로그 확인
sudo journalctl -u study-tracker -f

# DB 스키마 변경 시 (migration 미적용)
rm ~/study-tracker/study_tracker.db
sudo systemctl restart study-tracker
```

## 개발 시 주의사항

### 서버
- `datetime.now()` 사용 (utcnow 아님) — TZ=Asia/Seoul로 서버 시간이 KST
- bcrypt는 **4.0.1** 고정 (5.x는 passlib 호환 오류)
- DB 스키마 변경 시 기존 DB 파일 삭제 후 재시작 필요 (migration 미적용)
- **배포 후 반드시 `__pycache__` 삭제** — 남아있으면 이전 .pyc 실행됨
- `WorkingDirectory`가 `~/study-tracker`임에 주의 (scp 경로 혼동 주의)

### 클라이언트 EXE 빌드
- Python **3.11** 권장 (3.12+ PyInstaller/pynput 호환 이슈)
- pynput은 반드시 `--hidden-import` 명시 필요:
  ```
  --hidden-import=pynput.keyboard._win32
  --hidden-import=pynput.mouse._win32
  ```
- 빌드는 반드시 **Windows**에서 (Linux에서 Windows EXE 크로스컴파일 불가)
- 빌드 후 관리자 페이지 `/admin` → **클라이언트 배포** 탭에서 버전 + EXE 업로드

### HTML 페이지
- 순수 HTML + Vanilla JS (프레임워크 없음)
- 인증 토큰은 `localStorage`에 저장
- API 호출 시 `Authorization: Bearer <token>` 헤더 필수
- 관리자 체크: `role === 'admin'` 아닌 `['superadmin','group_admin'].includes(role)` 사용

## API 엔드포인트 요약

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| POST | `/auth/login` | 없음 | 로그인 → JWT 반환 |
| GET | `/client/version` | 없음 | 클라이언트 최신 버전 조회 |
| GET | `/client/download` | 없음 | 클라이언트 EXE 다운로드 |
| POST | `/admin/client/upload` | superadmin | 새 클라이언트 EXE 업로드 |
| GET | `/api/attendance/today` | member+ | 오늘 출퇴근 상태 조회 |
| POST | `/api/checkin` | member+ | 출근 |
| POST | `/api/checkout` | member+ | 퇴근 |
| POST | `/api/absence/start` | member+ | 외출 시작 (body: reason) |
| POST | `/api/absence/end` | member+ | 복귀 |
| POST | `/api/heartbeat` | member+ | 활동 시간 전송 (body: active_seconds) |
| POST | `/api/cheat-report` | member+ | 치트 감지 신고 (body: reason) |
| GET | `/api/stats` | member+ | 날짜별 랭킹 조회 |
| GET | `/admin/groups` | admin+ | 그룹 목록 |
| POST | `/admin/groups` | superadmin | 그룹 생성 |
| DELETE | `/admin/groups/{id}` | superadmin | 그룹 삭제 |
| GET | `/admin/users` | admin+ | 유저 목록 (group_admin은 자기 그룹만) |
| POST | `/admin/users` | admin+ | 유저 생성 |
| PATCH | `/admin/users/{id}` | admin+ | 유저 수정 |
| DELETE | `/admin/users/{id}` | admin+ | 유저 삭제 |
| GET | `/admin/attendance` | admin+ | 날짜별 출퇴근·외출 기록 |
| GET | `/admin/cheats` | admin+ | 날짜별 치트 감지 로그 |
