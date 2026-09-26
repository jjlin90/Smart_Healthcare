"""Run real Alembic upgrades against isolated databases, never the local .env DB."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def upgrade(path):
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path.as_posix()}"}
    return subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def test_fresh_database_upgrade_and_repeated_upgrade(tmp_path):
    path = tmp_path / "fresh.db"
    result = upgrade(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
        assert {"users", "patient_profiles", "patient_access", "tool_executions", "consultation_history", "audit_logs"} <= tables
        assert db.execute("select version_num from alembic_version").fetchone()[0] == "20260926_02"
    assert upgrade(path).returncode == 0


def test_incomplete_database_fails_without_silent_stamp(tmp_path):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as db:
        db.execute("create table unrelated (id integer)")
    result = upgrade(path)
    assert result.returncode != 0
    with sqlite3.connect(path) as db:
        assert db.execute("select count(*) from alembic_version").fetchone()[0] == 0


def test_legacy_patient_disabled_and_staff_access_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            create table patient_profiles (patient_id varchar(64) primary key);
            create table users (user_id varchar(64) primary key, patient_id varchar(64), role varchar(32), active boolean);
            insert into patient_profiles values ('p1');
            insert into users values ('s1','p1','doctor',1), ('u1','p1','patient',1);
        """)
    result = upgrade(path)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("select active from users where user_id='u1'").fetchone()[0] == 0
        assert db.execute("select user_id,patient_id from patient_access").fetchall() == [('s1', 'p1')]
