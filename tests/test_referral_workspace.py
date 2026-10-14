"""Exercise the real referral form with isolated HTTP boundaries."""

import ast
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


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
