from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from database import init_db, SessionLocal
from routers import auth_router, admin_router, api_router
from models import User
from auth import hash_password
from sqlalchemy import select
import os

app = FastAPI(title="Study Tracker")

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(api_router.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup():
    await init_db()
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.role == "admin"))
        if not result.scalar_one_or_none():
            admin = User(username="admin", password_hash=hash_password("admin1234"), role="admin")
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
