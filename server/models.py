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
    animal_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0~7, None=자동(username 해시)
    client_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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


class StudyGoal(Base):
    __tablename__ = "study_goals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # 개인 목표 (우선순위 최고)
    group_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("groups.id"), nullable=True)
    daily_target_minutes: Mapped[int] = mapped_column(Integer, default=480)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Feedback(Base):
    __tablename__ = "feedbacks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String, default="general")  # bug | suggestion | general
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_comment: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Notice(Base):
    __tablename__ = "notices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    group_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("groups.id"), nullable=True)  # None=전체
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class UserPoint(Base):
    __tablename__ = "user_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    seconds_buffer: Mapped[float] = mapped_column(Float, default=0.0)  # 포인트 미적립 잔여 초
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PointLog(Base):
    __tablename__ = "point_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)  # 양수=적립, 음수=소비
    reason: Mapped[str] = mapped_column(String)  # study | streak | ranking | purchase
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ShopItem(Base):
    __tablename__ = "shop_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    slot: Mapped[str] = mapped_column(String)  # hat | top | accessory
    price: Mapped[int] = mapped_column(Integer)
    svg_data: Mapped[str] = mapped_column(String)  # SVG 내용 직접 저장
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class UserInventory(Base):
    __tablename__ = "user_inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("shop_items.id"))
    purchased_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class UserEquip(Base):
    __tablename__ = "user_equips"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, index=True)
    slot: Mapped[str] = mapped_column(String)  # hat | top | accessory
    item_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("shop_items.id"), nullable=True)
