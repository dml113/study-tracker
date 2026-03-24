from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from database import init_db, SessionLocal
from routers import auth_router, admin_router, api_router
from models import User
from auth import hash_password, get_current_superadmin
from sqlalchemy import select
import os
import shutil

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


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("static/login.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return FileResponse("static/admin.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return FileResponse("static/dashboard.html")


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
