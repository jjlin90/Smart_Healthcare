"""Keep unit tests isolated from configured external observability services."""

import os


os.environ["LANGSMITH_TRACING"] = "false"
os.environ["REDIS_URL"] = ""
os.environ["APP_ENV"] = "test"
os.environ["INTERNAL_SERVICE_SECRET"] = "unit-test-internal-key-not-for-production"

import pytest
from fnmatch import fnmatchcase
from pathlib import Path
from backend.service_auth import delegated_context


@pytest.fixture
def clinical_delegation():
    with delegated_context({"staff_id": "test_staff", "patient_id": "authorized_patient", "request_id": "test-request-0001", "approved_actions": []}):
        yield


@pytest.fixture
def case_sensitive_markdown_globs(monkeypatch):
    """Make Markdown/text discovery obey POSIX case rules even on Windows."""
    def wrap(original):
        def glob(path, pattern, *args, **kwargs):
            if pattern in ('*.md', '*.txt'):
                return (candidate for candidate in original(path, '*', *args, **kwargs)
                        if fnmatchcase(candidate.name, pattern))
            return original(path, pattern, *args, **kwargs)
        return glob
    monkeypatch.setattr(Path, 'glob', wrap(Path.glob))
    monkeypatch.setattr(Path, 'rglob', wrap(Path.rglob))
