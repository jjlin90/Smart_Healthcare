from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class StaffLoginRequest(BaseModel):
    employee_id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChatRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    patient_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    approved_actions: list[Literal["save_patient_history", "save_medical_record", "generate_referral"]] = Field(default_factory=list, max_length=3)


class UserContext(BaseModel):
    username: str
    user_id: str
    role: Literal["doctor", "pharmacist", "medical_admin", "system_admin"]
    legacy_patient_id: str | None = None


ClinicalEntry = Annotated[str, Field(min_length=1, max_length=500)]


class HistoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allergies: list[ClinicalEntry] | None = Field(default=None, max_length=100)
    conditions: list[ClinicalEntry] | None = Field(default=None, max_length=100)
    medications: list[ClinicalEntry] | None = Field(default=None, max_length=100)


class RecordWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="临床辅助任务", min_length=1, max_length=128)
    input: dict = Field(default_factory=dict)
    response: str = Field(min_length=1, max_length=16000)
    intent: str = Field(default="辅助材料", max_length=32)
    department: str | None = Field(default=None, max_length=64)


class WriteToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1, max_length=64)


class HistoryToolArguments(WriteToolArguments):
    history_data: HistoryUpdate


class RecordToolArguments(WriteToolArguments):
    record_data: RecordWrite


class ReferralToolArguments(WriteToolArguments):
    department: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=4000)


def validate_write_arguments(name: str, arguments: dict) -> None:
    """Validate deterministic input errors before reserving a write operation."""
    schemas = {
        "save_patient_history": HistoryToolArguments,
        "save_medical_record": RecordToolArguments,
        "generate_referral": ReferralToolArguments,
    }
    parsed = schemas[name].model_validate(arguments)
    if isinstance(parsed, HistoryToolArguments) and not parsed.history_data.model_dump(exclude_none=True):
        raise ValueError("必须提供至少一项病史字段")
    if isinstance(parsed, ReferralToolArguments) and (not parsed.department.strip() or not parsed.reason.strip()):
        raise ValueError("转诊科室或原因无效")


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    age: int = Field(ge=0, le=150)
    gender: str = Field(min_length=1, max_length=10)
    allergies: list[ClinicalEntry] = Field(default_factory=list, max_length=100)
    conditions: list[ClinicalEntry] = Field(default_factory=list, max_length=100)
    medications: list[ClinicalEntry] = Field(default_factory=list, max_length=100)
