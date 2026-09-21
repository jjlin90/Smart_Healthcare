from typing import Literal

from pydantic import BaseModel, Field


class StaffLoginRequest(BaseModel):
    employee_id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChatRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class UserContext(BaseModel):
    username: str
    user_id: str
    role: Literal["doctor", "pharmacist", "medical_admin", "system_admin"]
    legacy_patient_id: str | None = None


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    age: int = Field(ge=0, le=150)
    gender: str = Field(min_length=1, max_length=10)
    allergies: list[str] = []
    conditions: list[str] = []
    medications: list[str] = []
