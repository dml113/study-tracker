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
            "ALTER TABLE study_goals ADD COLUMN username VARCHAR",
            "CREATE TABLE IF NOT EXISTS user_points (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, points INTEGER NOT NULL DEFAULT 0, seconds_buffer REAL NOT NULL DEFAULT 0, updated_at DATETIME)",
            "CREATE TABLE IF NOT EXISTS point_logs (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL, amount INTEGER NOT NULL, reason VARCHAR NOT NULL, created_at DATETIME)",
            "CREATE TABLE IF NOT EXISTS shop_items (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, slot VARCHAR NOT NULL, price INTEGER NOT NULL, svg_data TEXT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1, created_at DATETIME)",
            "CREATE TABLE IF NOT EXISTS user_inventory (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL, item_id INTEGER NOT NULL REFERENCES shop_items(id), purchased_at DATETIME)",
            "CREATE TABLE IF NOT EXISTS user_equips (id INTEGER PRIMARY KEY, username VARCHAR NOT NULL, slot VARCHAR NOT NULL, item_id INTEGER REFERENCES shop_items(id))",
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
