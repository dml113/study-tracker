# 동아리 공부 트래커

IT 동아리 부원들의 공부 시간을 추적하고 랭킹으로 보여주는 서비스.
키보드·마우스 활동을 감지해 실제로 PC 앞에 앉아 있는 시간을 측정합니다.

---

## 주요 기능

- **출근 / 퇴근** — 출근 버튼을 누른 시간부터만 측정
- **외출** — 사유 입력 후 외출 처리, 해당 시간은 측정 제외
- **식사 시간 자동 제외** — 평일/주말 점심·저녁 시간 자동 제외
- **치트 감지** — 키보드에 물건 올리기·매크로 자동 감지 및 측정 중단
- **일간 / 주간 / 월간 랭킹** — 기간별 누적 공부 시간 대시보드
- **목표 시간 설정** — 그룹별/전체 하루 목표 시간 + 달성률 표시
- **실시간 출석 현황** — 지금 누가 출근 중인지 대시보드에서 확인
- **내 통계 페이지** — 일별 활동 차트, 주간 요약, 연속 달성 스트릭
- **알 부화 시스템** — 누적 공부시간에 따라 알이 자라고 동물로 부화 (SVG 일러스트)
- **비밀번호 변경** — 부원이 클라이언트 앱에서 직접 변경 가능
- **외출 사유 통계** — 기간별 외출 사유 빈도 관리자 조회
- **DB 자동 백업** — 매일 자정 자동 백업, 최근 7일치 보관
- **그룹 관리** — C.C, IT, C.S 등 소그룹별 관리자 지정
- **클라이언트 자동 업데이트** — 서버에 새 EXE 올리면 다음 실행 시 자동 업데이트
- **GitHub Actions 자동 빌드** — 버전 파일만 수정하면 EXE 자동 빌드 및 Release 업로드
- **피드백 / 버그 신고** — 부원이 직접 버그·기능제안·의견 제출, 관리자 페이지에서 확인/삭제
- **공지사항** — 관리자가 공지 등록, 부원 로그인 시 팝업으로 표시
- **동물 관리** — 슈퍼어드민이 유저별 동물 직접 지정 (미지정 시 username 해시 자동 배정)
- **Windows EXE** — Python 없이 EXE 파일 하나로 실행

---

## 구조

```
study-tracker/
├── server/
│   ├── main.py              # 앱 진입점, 클라이언트 배포 엔드포인트, 수동 백업 엔드포인트
│   ├── models.py            # DB 모델 (User, Group, ActivityLog, Attendance, Absence, CheatLog, StudyGoal, Feedback)
│   ├── auth.py              # JWT, 비밀번호 해싱, 권한 검증
│   ├── database.py          # DB 엔진, 세션, 자동 마이그레이션
│   ├── backup.py            # DB 자동 백업 스케줄러 (매일 자정, 최근 7일 보관)
│   ├── routers/
│   │   ├── auth_router.py
│   │   ├── admin_router.py
│   │   └── api_router.py
│   ├── static/
│   │   ├── login.html       # 로그인 + 클라이언트 다운로드
│   │   ├── admin.html       # 관리자 페이지
│   │   ├── dashboard.html   # 랭킹 대시보드 (일간/주간/월간, 출석 현황, 달성률, 알 시스템)
│   │   ├── me.html          # 내 통계 (일별 차트, 주간 요약, 알 부화 현황)
│   │   └── feedback.html    # 피드백/버그 신고 페이지
│   └── client_dist/         # 배포용 EXE + version.txt (자동 생성)
├── client/
│   ├── client.py            # Windows 클라이언트 (비밀번호 변경 포함)
│   ├── build.bat            # EXE 빌드 스크립트 (로컬용)
│   └── VERSION              # 버전 파일 (변경 시 자동 빌드 트리거)
└── .github/workflows/
    └── build-client.yml     # GitHub Actions 자동 빌드
```

---

## 역할 체계

| 역할 | 설명 |
|------|------|
| `superadmin` | 슈퍼 어드민 (1개). 그룹 생성/삭제, 모든 유저 관리, 클라이언트 배포, DB 백업 |
| `group_admin` | 그룹 어드민. 자기 그룹 멤버만 생성/수정/삭제/조회, 그룹 목표 설정 |
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
| `/me` | 내 통계 페이지 |
| `/feedback` | 피드백 / 버그 신고 |

### 대시보드 기능

| 기능 | 설명 |
|------|------|
| 일간/주간/월간 탭 | 기간별 누적 공부 시간 랭킹 |
| 목표 달성률 | 각 카드에 목표 대비 달성 % 프로그레스 바 |
| 실시간 출석 현황 | 현재 출근 중 / 외출 중 멤버 표시 (30초 갱신) |
| 알 부화 시스템 | 유저별 알 SVG + 링 프로그레스 표시 |

### 내 통계 페이지 기능

| 기능 | 설명 |
|------|------|
| 알 부화 현황 | 내 알 종류 + 단계 + 다음 단계까지 남은 시간 |
| 일별 활동 차트 | 최근 30/60/90일 바 차트 |
| 주간 요약 | 최근 4주 활동 시간 카드 |
| 스탯 카드 | 총 활동시간, 출석일, 현재/최대 연속 스트릭 |

### 알 부화 시스템

각 유저는 username 해시 기반으로 8종 중 하나의 알을 배정받습니다. 슈퍼어드민이 관리자 페이지에서 동물을 직접 지정할 수도 있습니다. 누적 공부시간에 따라 단계가 올라갑니다.

| 단계 | 조건 | 표시 |
|------|------|------|
| 0단계 | 0 ~ 5시간 | 자는 알 (ZZZ) |
| 1단계 | 5 ~ 15시간 | 금 가는 알 (눈 뜨기 시작) |
| 2단계 | 15 ~ 30시간 | 깨지는 알 (발 삐죽) |
| 3단계 | 30시간+ | 동물 부화 완료 🎉 |

| 번호 | 동물 | 특징 |
|------|------|------|
| 0 | 고양이 | 복숭아 얼굴, 파란 눈, 수염 |
| 1 | 강아지 | 황금색, 축 처진 귀, 혀 |
| 2 | 햄스터 | 통통한 볼주머니, 큰 눈 |
| 3 | 토끼 | 긴 귀, 분홍 눈 |
| 4 | 개구리 | 눈이 머리 위, 넓은 미소 |
| 5 | 여우 | 주황색, 흰 뺨 |
| 6 | 판다 | 흑백, 검정 눈 패치 |
| 7 | 코알라 | 회색, 큰 귀와 코 |

### 관리자 페이지 탭

| 탭 | 설명 |
|----|------|
| 유저 관리 | 유저 생성/삭제/비활성화, 그룹 배정, 역할 변경 |
| 공부 통계 | 날짜별 활동 시간 랭킹 |
| 출퇴근 기록 | 날짜별 출근·퇴근·외출 기록 |
| 치트 감지 | 날짜별 비정상 입력 감지 로그 |
| 목표 설정 | 그룹별/전체 하루 목표 시간 설정 |
| 외출 통계 | 기간별 외출 사유 빈도 + 유저별 통계 |
| 💬 피드백 | 부원이 제출한 버그·기능제안·의견 조회 및 삭제 |
| 📢 공지 관리 | 공지 등록/활성화·비활성화/삭제 (슈퍼 어드민 전용) |
| 🐾 동물 관리 | 유저별 동물 직접 지정 (슈퍼 어드민 전용) |
| ✏️ 기록 수정 | 활동 기록 직접 추가/수정 (슈퍼 어드민 전용) |
| 그룹 관리 | 그룹 생성/삭제 (슈퍼 어드민 전용) |
| 클라이언트 배포 | EXE 업로드, GitHub 동기화, 버전 관리 (슈퍼 어드민 전용) |
| DB 백업 | 수동 백업 실행, 백업 파일 목록 조회 (슈퍼 어드민 전용) |

---

## 클라이언트 사용법 (Windows)

1. `StudyTracker.exe` 실행
2. 서버 주소, 아이디, 비밀번호 입력 후 **입장하기** 클릭
3. **출근 🚀** 버튼 클릭 → 측정 시작
4. 자리 비울 때 **외출** → 사유 입력
5. 돌아오면 **복귀 ✨**
6. 퇴근 시 **퇴근** 버튼
7. 하단 **비밀번호 변경** 버튼으로 직접 비밀번호 변경 가능
8. **웹 대시보드** 버튼으로 랭킹/알 현황 브라우저에서 확인 가능
9. 로그인 시 **공지사항**이 있으면 자동 팝업으로 표시

한 번 로그인하면 다음 실행부터 자동 로그인됩니다.

---

## 치트 감지

| 패턴 | 감지 조건 |
|------|---------|
| 키보드에 물건 올리기 | 30초 내 같은 키가 75% 이상 |
| 매크로 | 입력 간격 평균 <500ms + 표준편차 <30ms |

감지 시 활동 측정 즉시 중단 + 서버 자동 신고. 정상 입력 재개 시 자동 해제됩니다.

---

## DB 백업

- **자동**: 매일 자정 SQLite 파일 복사 (`backups/study_tracker_YYYYMMDD_HHMMSS.db`)
- **수동**: 관리자 페이지 → DB 백업 탭 → "지금 백업" 버튼
- **보관**: 최근 7개 파일만 유지, 오래된 파일 자동 삭제

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
