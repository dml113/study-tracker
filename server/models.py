from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy import Integer, String, Float, DateTime, Boolean, ForeignKey
from datetime import datetime
from typing import Optional


class Base(DeclarativeBase):
    pass


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="member")  # superadmin | group_admin | member
    group_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("groups.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String, index=True)
    active_seconds: Mapped[float] = mapped_column(Float, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Attendance(Base):
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String, index=True)
    checkin_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    checkout_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Absence(Base):
    __tablename__ = "absences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(String)


class CheatLog(Base):
    __tablename__ = "cheat_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    reason: Mapped[str] = mapped_column(String)
