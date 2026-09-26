"""Keep unit tests isolated from configured external observability services."""

import os


os.environ["LANGSMITH_TRACING"] = "false"
os.environ["REDIS_URL"] = ""
os.environ["APP_ENV"] = "test"
os.environ["INTERNAL_SERVICE_SECRET"] = "unit-test-internal-key-not-for-production"

import pytest
from backend.service_auth import delegated_context


@pytest.fixture
def clinical_delegation():
    with delegated_context({"staff_id": "test_staff", "patient_id": "authorized_patient", "request_id": "test-request-0001", "approved_actions": []}):
        yield
