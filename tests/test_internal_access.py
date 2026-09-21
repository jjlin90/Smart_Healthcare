from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from pydantic import ValidationError

from backend.auth import ALGORITHM, get_current_user
from backend.config import get_settings
from backend.main import app, authorized_patient_ids, liveness, readiness
from backend.schemas import ChatRequest, UserContext


def test_public_patient_login_is_not_exposed():
    paths = {route.path for route in app.routes}
    assert "/api/auth/staff-login" in paths
    assert "/api/auth/patient-login" not in paths
    assert "/api/auth/doctor-login" not in paths


def test_only_hospital_staff_roles_are_accepted():
    with pytest.raises(ValidationError):
        UserContext(username="patient", user_id="u1", role="patient")
    for role in ("doctor", "pharmacist", "medical_admin", "system_admin"):
        context = UserContext(username="staff", user_id="u1", role=role)
        assert context.role == role


def test_clinical_request_requires_explicit_patient_scope():
    with pytest.raises(ValidationError):
        ChatRequest(message="请分析症状")
    request = ChatRequest(patient_id="patient_001", message="请分析症状和当前用药")
    assert request.patient_id == "patient_001"


@pytest.mark.asyncio
async def test_staff_patient_access_combines_mapping_and_legacy_scope():
    class FakeScalars:
        def all(self):
            return ["patient_002"]

    class FakeDb:
        async def scalars(self, _query):
            return FakeScalars()

    user = UserContext(
        username="doctor",
        user_id="staff_001",
        role="doctor",
        legacy_patient_id="patient_001",
    )
    assert await authorized_patient_ids(FakeDb(), user) == {"patient_001", "patient_002"}


@pytest.mark.asyncio
async def test_medical_admin_can_use_hospital_wide_scope():
    user = UserContext(username="admin", user_id="admin_001", role="medical_admin")
    assert await authorized_patient_ids(SimpleNamespace(), user) is None


@pytest.mark.asyncio
async def test_system_admin_has_no_implicit_clinical_data_scope():
    class FakeScalars:
        def all(self):
            return []

    class FakeDb:
        async def scalars(self, _query):
            return FakeScalars()

    user = UserContext(username="ops", user_id="system_001", role="system_admin")
    assert await authorized_patient_ids(FakeDb(), user) == set()


@pytest.mark.asyncio
async def test_empty_jwt_secret_fails_closed(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", "")
    forged = jwt.encode(
        {"username": "forged", "user_id": "forged", "role": "medical_admin"},
        "",
        algorithm=ALGORITHM,
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=forged)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_orchestrator_health_endpoints_separate_live_and_ready(monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "")
    response = Response()
    payload = await readiness(response)
    assert response.status_code == 503
    assert payload["status"] == "configuration_required"
    assert await liveness() == {"status": "alive", "service": "MedAgent AI"}
