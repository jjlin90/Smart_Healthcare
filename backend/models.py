from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    age: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(10))
    allergy_history: Mapped[str] = mapped_column(Text, default="[]")
    past_medical_history: Mapped[str] = mapped_column(Text, default="[]")
    current_medications: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    employee_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    patient_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PatientAccess(Base):
    __tablename__ = "patient_access"
    __table_args__ = (UniqueConstraint("user_id", "patient_id", name="uq_patient_access_user_patient"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patient_profiles.patient_id"), index=True)
    granted_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Consultation(Base):
    __tablename__ = "consultation_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patient_profiles.patient_id"), index=True
    )
    title: Mapped[str] = mapped_column(String(128))
    symptoms: Mapped[str] = mapped_column(Text)
    response: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(32))
    suggested_department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    patient: Mapped[PatientProfile] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    department: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="待医生确认")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
