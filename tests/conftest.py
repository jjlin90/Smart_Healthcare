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


def pytest_collect_file(file_path, parent):
    """Enforce canonical suffixes for Python files encountered under tests."""
    if file_path.suffix.lower() == ".py" and file_path.suffix != ".py":
        raise pytest.UsageError(
            f"{file_path.name}: 测试目录中的 Python 文件必须使用小写 .py 后缀，请按项目命名约定改名"
        )


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
