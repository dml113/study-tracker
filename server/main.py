from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from database import init_db, SessionLocal
from routers import auth_router, admin_router, api_router
from models import User
from auth import hash_password, get_current_superadmin
from backup import backup_scheduler, run_backup, list_backups, auto_checkout_scheduler, weekly_report_scheduler, daily_report_scheduler, morning_checkin_scheduler
from sqlalchemy import select
import asyncio
import os
import re
import shutil
import urllib.request

CLIENT_DIR = "client_dist"
VERSION_FILE = os.path.join(CLIENT_DIR, "version.txt")
ZIP_FILE = os.path.join(CLIENT_DIR, "StudyTracker.zip")

app = FastAPI(title="Study Tracker")


@app.middleware("http")
async def redirect_typo_domain(request: Request, call_next):
    host = request.headers.get("host", "")
    if host.startswith("traker."):
        new_host = host.replace("traker.", "tracker.", 1)
        url = request.url.replace(netloc=new_host)
        return RedirectResponse(url=str(url), status_code=301)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(api_router.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    os.makedirs(CLIENT_DIR, exist_ok=True)
    if not os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "w") as f:
            f.write("1.0.0")
    await init_db()
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(username="admin", password_hash=hash_password("admin1234"), role="superadmin")
            session.add(admin)
            await session.commit()
            print("기본 관리자 계정 생성: admin / admin1234")
    asyncio.create_task(backup_scheduler())
    asyncio.create_task(auto_checkout_scheduler())
    asyncio.create_task(weekly_report_scheduler())
    asyncio.create_task(daily_report_scheduler())
    asyncio.create_task(morning_checkin_scheduler())


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("static/login.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return FileResponse("static/admin.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return FileResponse("static/dashboard.html")


@app.get("/me", response_class=HTMLResponse)
async def me_page():
    return FileResponse("static/me.html")


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_page():
    return FileResponse("static/feedback.html")


# ── 클라이언트 배포 ────────────────────────────────────

@app.get("/client/version")
async def client_version():
    if not os.path.exists(VERSION_FILE):
        return {"version": "0.0.0"}
    with open(VERSION_FILE) as f:
        return {"version": f.read().strip()}


@app.get("/client/download")
async def client_download():
    if not os.path.exists(ZIP_FILE):
        raise HTTPException(status_code=404, detail="배포된 클라이언트가 없습니다")
    return FileResponse(ZIP_FILE, filename="StudyTracker.zip", media_type="application/zip")


_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


@app.post("/admin/client/upload")
async def upload_client(
    version: str,
    file: UploadFile = File(...),
    _: dict = Depends(get_current_superadmin),
):
    if not _VERSION_RE.match(version):
        raise HTTPException(status_code=400, detail="버전 형식이 올바르지 않습니다 (예: 1.2.3)")
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail=".zip 파일만 업로드 가능합니다")
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일 크기가 200MB를 초과합니다")
    with open(ZIP_FILE, "wb") as f:
        f.write(data)
    with open(VERSION_FILE, "w") as f:
        f.write(version)
    return {"message": f"버전 {version} 업로드 완료"}


GITHUB_REPO = "dml113/study-tracker"

@app.post("/admin/client/sync-github")
async def sync_from_github(_: dict = Depends(get_current_superadmin)):
    import json
    github_token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": "study-tracker-server", "Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            release = json.loads(res.read())
    except Exception as e:
        import logging; logging.error(f"GitHub API 오류: {e}")
        raise HTTPException(status_code=502, detail="GitHub API 오류. 서버 로그를 확인하세요.")

    version = release["tag_name"].lstrip("v")
    zip_asset = next(
        (a for a in release.get("assets", []) if a["name"].endswith(".zip")), None
    )
    if not zip_asset:
        raise HTTPException(status_code=404, detail="Release에 ZIP 파일이 없습니다")

    try:
        dl_headers = {**headers, "Accept": "application/octet-stream"}
        dl_req = urllib.request.Request(zip_asset["url"], headers=dl_headers)
        with urllib.request.urlopen(dl_req, timeout=60) as res:
            with open(ZIP_FILE, "wb") as f:
                f.write(res.read())
    except Exception as e:
        import logging; logging.error(f"GitHub 다운로드 오류: {e}")
        raise HTTPException(status_code=502, detail="다운로드 오류. 서버 로그를 확인하세요.")

    with open(VERSION_FILE, "w") as f:
        f.write(version)

    return {"message": f"v{version} 동기화 완료"}


@app.post("/admin/backup")
async def manual_backup(_: dict = Depends(get_current_superadmin)):
    dest = await run_backup()
    if not dest:
        raise HTTPException(status_code=404, detail="백업할 DB 파일이 없습니다")
    return {"message": f"백업 완료: {os.path.basename(dest)}"}


@app.get("/admin/backups")
async def get_backups(_: dict = Depends(get_current_superadmin)):
    return list_backups()
