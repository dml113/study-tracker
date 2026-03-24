from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from database import init_db, SessionLocal
from routers import auth_router, admin_router, api_router
from models import User
from auth import hash_password, get_current_superadmin
from backup import backup_scheduler, run_backup, list_backups
from sqlalchemy import select
import asyncio
import os
import shutil
import urllib.request

CLIENT_DIR = "client_dist"
VERSION_FILE = os.path.join(CLIENT_DIR, "version.txt")
EXE_FILE = os.path.join(CLIENT_DIR, "StudyTracker.exe")

app = FastAPI(title="Study Tracker")

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


# ── 클라이언트 배포 ────────────────────────────────────

@app.get("/client/version")
async def client_version():
    if not os.path.exists(VERSION_FILE):
        return {"version": "0.0.0"}
    with open(VERSION_FILE) as f:
        return {"version": f.read().strip()}


@app.get("/client/download")
async def client_download():
    if not os.path.exists(EXE_FILE):
        raise HTTPException(status_code=404, detail="배포된 클라이언트가 없습니다")
    return FileResponse(EXE_FILE, filename="StudyTracker.exe", media_type="application/octet-stream")


@app.post("/admin/client/upload")
async def upload_client(
    version: str,
    file: UploadFile = File(...),
    _: dict = Depends(get_current_superadmin),
):
    if not file.filename.endswith(".exe"):
        raise HTTPException(status_code=400, detail=".exe 파일만 업로드 가능합니다")
    with open(EXE_FILE, "wb") as f:
        shutil.copyfileobj(file.file, f)
    with open(VERSION_FILE, "w") as f:
        f.write(version)
    return {"message": f"버전 {version} 업로드 완료"}


GITHUB_REPO = "dml113/study-tracker"

@app.post("/admin/client/sync-github")
async def sync_from_github(_: dict = Depends(get_current_superadmin)):
    import json
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": "study-tracker-server"},
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            release = json.loads(res.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API 오류: {e}")

    version = release["tag_name"].lstrip("v")
    exe_asset = next(
        (a for a in release.get("assets", []) if a["name"].endswith(".exe")), None
    )
    if not exe_asset:
        raise HTTPException(status_code=404, detail="Release에 EXE 파일이 없습니다")

    try:
        urllib.request.urlretrieve(exe_asset["browser_download_url"], EXE_FILE)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"다운로드 오류: {e}")

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
