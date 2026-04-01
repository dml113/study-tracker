from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from models import Base

DATABASE_URL = "sqlite+aiosqlite:///./study_tracker.db"
engine = create_async_engine(DATABASE_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 스키마 마이그레이션: 새 컬럼 추가 (이미 있으면 무시)
        for stmt in [
            "ALTER TABLE users ADD COLUMN animal_type INTEGER",
            "ALTER TABLE users ADD COLUMN client_version VARCHAR",
            "CREATE TABLE IF NOT EXISTS feedbacks (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL, category VARCHAR NOT NULL DEFAULT 'general', title VARCHAR NOT NULL, body VARCHAR NOT NULL, created_at DATETIME)",
            "CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY, title VARCHAR NOT NULL, body VARCHAR NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME)",
            "ALTER TABLE feedbacks ADD COLUMN is_resolved BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE feedbacks ADD COLUMN admin_comment VARCHAR",
            "ALTER TABLE notices ADD COLUMN group_id INTEGER REFERENCES groups(id)",
        ]:
            try:
                await conn.execute(text(stmt))
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    print(f"[마이그레이션 경고] {stmt[:60]}... : {e}")


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
