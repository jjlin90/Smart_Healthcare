from typing import Literal

from pydantic import BaseModel, Field


class PatientLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    verification_code: str = Field(min_length=4, max_length=8)


class DoctorLoginRequest(BaseModel):
    employee_id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class UserContext(BaseModel):
    username: str
    user_id: str
    patient_id: str
    role: Literal["patient", "doctor"]


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    age: int = Field(ge=0, le=150)
    gender: str = Field(min_length=1, max_length=10)
    allergies: list[str] = []
    conditions: list[str] = []
    medications: list[str] = []
