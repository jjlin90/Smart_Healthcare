"""Exercise the real referral form with isolated HTTP boundaries."""

import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize("case", ["doctor", "doctor_unknown", "pharmacist", "medical_admin", "system_admin"])
def test_full_workspace_login_exposes_referral_only_to_doctor_and_submits_once(monkeypatch, case):
    import httpx
    role = "doctor" if case == "doctor_unknown" else case
    posts = []
    def get(url, **kwargs):
        if url.endswith("/api/patients"):
            return httpx.Response(200, json=[{"patient_id": "patient-a", "name": "Test", "age": 30, "gender": "unknown"}])
        if url.endswith("/profile"):
            return httpx.Response(200, json={"patient_id": "patient-a", "name": "Test", "age": 30, "gender": "unknown",
                                            "allergies": [], "conditions": [], "medications": [], "revision": "test-revision"})
        if "/api/operations/" in url:
            return httpx.Response(200, json=[{"tool": "generate_referral", "status": "unknown"}])
        if url.endswith("/api/patients/patient-a/consultations"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected workspace request: {url}")
    def post(url, **kwargs):
        posts.append((url, kwargs["json"].copy()))
        if url.endswith("/api/auth/staff-login"):
            return httpx.Response(200, json={"access_token": "offline-role-test", "token_type": "bearer", "role": role})
        assert url.endswith("/api/referral-drafts") and role == "doctor"
        assert kwargs["headers"] == {"Authorization": "Bearer offline-role-test"}
        if case == "doctor_unknown":
            return httpx.Response(502, json={"detail": "结果未确认，请核对操作状态与医院结果"})
        return httpx.Response(201, json={"referral_id": "ref-full-workspace", "status": "待医生确认"})
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run(timeout=10)
    next(w for w in app.text_input if w.label == "员工工号").set_value("D001")
    next(w for w in app.text_input if w.label == "密码").set_value("test-password-123")
    next(w for w in app.button if w.label == "院内账号登录").click().run(timeout=10)
    assert not app.exception
    assert app.session_state["staff_role"] == role and app.session_state["patient_id"] == "patient-a"
    assert any(w.label == "创建转诊草稿（仅医生）" for w in app.expander)
    if role != "doctor":
        assert not any(w.label == "确认并创建草稿" for w in app.button)
        assert len(posts) == 1
        return
    next(w for w in app.text_input if w.label == "医生确认的目标科室").set_value("心内科")
    next(w for w in app.text_area if w.label == "医生确认的转诊原因").set_value("医生确认的专科评估原因")
    next(w for w in app.checkbox if "确认创建本次草稿" in w.label).set_value(True)
    next(w for w in app.button if w.label == "确认并创建草稿").click().run(timeout=10)
    assert not app.exception
    referrals = [(url, payload) for url, payload in posts if url.endswith("/api/referral-drafts")]
    assert len(referrals) == 1
    payload = referrals[0][1]
    assert {k: payload[k] for k in ("patient_id", "department", "reason", "confirmed")} == {
        "patient_id": "patient-a", "department": "心内科", "reason": "医生确认的专科评估原因", "confirmed": True}
    assert len(payload["request_id"]) == 32
    if case == "doctor_unknown":
        app.run(timeout=10)
        next(w for w in app.button if w.label == "核对操作状态").click().run(timeout=10)
        assert not app.exception
        assert not any(w.label in {"使用原参数取回结果或重试", "新建另一份转诊草稿"} for w in app.button)
    else:
        assert any("ref-full-workspace" in w.value for w in app.success)
        app.run(timeout=10)
    assert len([url for url, _ in posts if url.endswith("/api/referral-drafts")]) == 1


@pytest.mark.parametrize("case", ["confirmed", "unconfirmed", "unknown", "pharmacist"])
def test_referral_form_confirms_exact_fields_and_keeps_unknown_submission(case):
    source = Path(__file__).resolve().parents[1].joinpath("streamlit_app.py").read_text(encoding="utf-8")
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "_render_referral_draft")
    renderer = ast.get_source_segment(source, node)
    harness = f'''
import streamlit as st
from uuid import uuid4
import httpx
st.session_state.setdefault("patient_id", "patient-a")
st.session_state.setdefault("staff_role", {"pharmacist" if case == "pharmacist" else "doctor"!r})
st.session_state.setdefault("referral_submissions", {{}})
st.session_state.setdefault("posts", [])
def _post(path, payload):
    st.session_state.posts.append((path, payload.copy()))
    if {case == "unknown"!r}:
        raise RuntimeError("result unknown")
    return {{"referral_id": "ref-1", "status": "待医生确认"}}
def _get(path):
    return [{{"tool": "generate_referral", "status": "unknown"}}]
{renderer}
_render_referral_draft()
'''
    app = AppTest.from_string(harness).run()
    assert not app.exception
    if case == "pharmacist":
        assert not app.text_input and not app.button
        return
    app.text_input[0].set_value("心内科")
    app.text_area[0].set_value("医生确认的专科评估原因")
    app.checkbox[0].set_value(case != "unconfirmed")
    next(button for button in app.button if button.label == "确认并创建草稿").click().run()
    assert not app.exception
    posts = app.session_state["posts"]
    if case == "unconfirmed":
        assert posts == [] and app.warning
        return
    assert len(posts) == 1 and posts[0][0] == "/api/referral-drafts"
    payload = posts[0][1]
    assert payload["patient_id"] == "patient-a" and payload["department"] == "心内科"
    assert payload["reason"] == "医生确认的专科评估原因" and payload["confirmed"] is True
    assert len(payload["request_id"]) == 32
    if case == "confirmed":
        assert app.success
    else:
        app.run()
        next(button for button in app.button if button.label == "核对操作状态").click().run()
        assert not app.exception
        assert len(app.session_state["posts"]) == 1
        assert not any(button.label in {"使用原参数取回结果或重试", "新建另一份转诊草稿"} for button in app.button)
        app.session_state["patient_id"] = "patient-b"
        app.run()
        assert app.session_state["referral_submissions"]["patient-a"]["payload"] == payload
