"""Streamlit patient/doctor workspace for the MedAgent FastAPI service."""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx
import streamlit as st


API_BASE_URL = os.getenv("MEDAGENT_API_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=10.0)

st.set_page_config(
    page_title="MedAgent AI",
    page_icon="⚕",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _init_state() -> None:
    defaults = {
        "token": None,
        "role": "patient",
        "messages": [],
        "conversation_id": None,
        "agent_trace": [],
        "profile": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state.token}"}


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("detail") or payload)
    except (ValueError, TypeError):
        return response.text or f"HTTP {response.status_code}"


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = httpx.post(
        f"{API_BASE_URL}{path}",
        json=payload,
        headers=_headers() if st.session_state.token else {},
        timeout=REQUEST_TIMEOUT,
    )
    if response.is_error:
        raise RuntimeError(_error_message(response))
    return response.json()


def _get(path: str) -> Any:
    response = httpx.get(
        f"{API_BASE_URL}{path}",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    if response.is_error:
        raise RuntimeError(_error_message(response))
    return response.json()


def _put(path: str, payload: dict[str, Any]) -> Any:
    response = httpx.put(
        f"{API_BASE_URL}{path}",
        json=payload,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    if response.is_error:
        raise RuntimeError(_error_message(response))
    return response.json()


def _sse_events(message: str) -> Iterator[tuple[str, dict[str, Any]]]:
    payload = {
        "message": message,
        "conversation_id": st.session_state.conversation_id,
    }
    with httpx.stream(
        "POST",
        f"{API_BASE_URL}/api/chat/stream",
        json=payload,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    ) as response:
        if response.is_error:
            response.read()
            raise RuntimeError(_error_message(response))
        event_name = "message"
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                raw = line.removeprefix("data:").strip()
                yield event_name, json.loads(raw)


def _logout() -> None:
    for key in ("token", "messages", "conversation_id", "agent_trace", "profile"):
        st.session_state[key] = None if key in {"token", "conversation_id", "profile"} else []
    st.rerun()


def _render_css() -> None:
    st.markdown(
        """
        <style>
        :root { --navy:#132238; --teal:#0d8a87; --mint:#e9f7f4; --line:#dfe7e9; }
        .stApp { background:#f7faf9; color:var(--navy); }
        [data-testid="stHeader"] { background:transparent; }
        [data-testid="stSidebar"] { background:#10283a; }
        [data-testid="stSidebar"] * { color:#eef8f6; }
        [data-testid="stSidebar"] .stButton button {
          width:100%; border:1px solid #456173; background:#17384d; color:white;
        }
        .med-brand { font-size:1.35rem; font-weight:750; letter-spacing:-.02em; margin:.5rem 0 1.25rem; }
        .med-brand span { color:#55d3c7; }
        .hero { padding:.8rem 0 1.2rem; border-bottom:1px solid var(--line); margin-bottom:1rem; }
        .hero h1 { font-size:2rem; margin:0; letter-spacing:-.035em; color:var(--navy); }
        .hero p { color:#5f707a; margin:.35rem 0 0; }
        .status { display:inline-flex; align-items:center; gap:.45rem; color:#54706e; font-size:.86rem; }
        .status-dot { width:.55rem; height:.55rem; background:#22a890; border-radius:50%; display:inline-block; }
        .login-heading { text-align:center; margin:4vh 0 1.25rem; }
        .login-heading h1 { color:var(--navy); margin:.3rem 0; letter-spacing:-.04em; }
        .login-heading p { color:#65747c; }
        .notice { padding:.8rem 1rem; border-left:3px solid var(--teal); background:var(--mint);
          border-radius:0 8px 8px 0; color:#365f5d; font-size:.9rem; }
        [data-testid="stChatMessage"] { background:white; border:1px solid #e2e9ea; border-radius:14px; padding:.55rem; }
        [data-testid="stChatInput"] { border-color:#bdd8d5; }
        .profile-label { color:#718087; font-size:.8rem; margin-bottom:.1rem; }
        .profile-value { color:var(--navy); font-weight:650; margin-bottom:.8rem; }
        div.stButton > button[kind="primary"] { background:var(--teal); border-color:var(--teal); }
        @media (max-width: 760px) { .login-heading { margin-top:1rem; } .hero h1 { font-size:1.55rem; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_login() -> None:
    _, center, _ = st.columns([1, 1.15, 1])
    with center:
        st.markdown(
            '<div class="login-heading"><h1>MedAgent AI</h1><p>智能医疗多代理工作台</p></div>',
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            role = st.segmented_control(
                "登录身份",
                options=["patient", "doctor"],
                format_func=lambda item: "患者端" if item == "patient" else "医生端",
                default=st.session_state.role,
                key="login_role",
            )
            st.session_state.role = role or "patient"
            with st.form("login_form", clear_on_submit=False):
                if st.session_state.role == "patient":
                    login_id = st.text_input("手机号", placeholder="请输入患者手机号")
                    credential = st.text_input("短信验证码", placeholder="由真实短信服务发送")
                    endpoint = "/api/auth/patient-login"
                    payload = {"phone": login_id, "verification_code": credential}
                else:
                    login_id = st.text_input("医生工号", placeholder="请输入医院工号")
                    credential = st.text_input("密码", type="password")
                    endpoint = "/api/auth/doctor-login"
                    payload = {"employee_id": login_id, "password": credential}
                submitted = st.form_submit_button("安全登录", type="primary", use_container_width=True)
            st.markdown(
                '<div class="notice">医疗建议仅供医生参考，不替代诊断；紧急情况请立即拨打 120。</div>',
                unsafe_allow_html=True,
            )
    if submitted:
        try:
            result = _post(endpoint, payload)
            st.session_state.token = result["access_token"]
            st.session_state.messages = []
            st.rerun()
        except (httpx.HTTPError, RuntimeError) as exc:
            st.error(f"登录失败：{exc}")


def _load_profile() -> dict[str, Any] | None:
    if st.session_state.profile is None:
        try:
            st.session_state.profile = _get("/api/profile")
        except (httpx.HTTPError, RuntimeError) as exc:
            st.warning(f"档案加载失败：{exc}")
    return st.session_state.profile


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<div class="med-brand">MedAgent <span>AI</span></div>', unsafe_allow_html=True)
        if st.button("＋ 新建问诊", use_container_width=True):
            st.session_state.messages = []
            st.session_state.conversation_id = None
            st.session_state.agent_trace = []
            st.rerun()
        st.markdown("#### 历史问诊")
        try:
            history = _get("/api/consultations")
            if not history:
                st.caption("暂无问诊记录")
            for item in history[:12]:
                st.markdown(f"**{item['title']}**")
                st.caption(item.get("intent") or "待识别")
        except (httpx.HTTPError, RuntimeError):
            st.caption("历史记录暂不可用")
        st.divider()
        st.caption("本地意图模型 · A2A · MCP")
        if st.button("退出登录", use_container_width=True):
            _logout()


def _render_profile(profile: dict[str, Any] | None) -> None:
    st.markdown("### 患者档案")
    if not profile:
        st.caption("暂无档案")
        return
    for label, value in (
        ("姓名", profile.get("name") or "未登记"),
        ("年龄", profile.get("age") or "未登记"),
        ("性别", profile.get("gender") or "未登记"),
        ("过敏史", "、".join(profile.get("allergies", [])) or "无记录"),
        ("既往病史", "、".join(profile.get("conditions", [])) or "无记录"),
        ("当前用药", "、".join(profile.get("medications", [])) or "无记录"),
    ):
        st.markdown(f'<div class="profile-label">{label}</div><div class="profile-value">{value}</div>', unsafe_allow_html=True)
    with st.expander("编辑档案"):
        with st.form("profile_form"):
            name = st.text_input("姓名", value=profile.get("name") or "")
            age = st.number_input("年龄", min_value=0, max_value=150, value=int(profile.get("age") or 0))
            gender = st.text_input("性别", value=profile.get("gender") or "")
            allergies = st.text_input("过敏史（逗号分隔）", value="，".join(profile.get("allergies", [])))
            conditions = st.text_input("既往病史（逗号分隔）", value="，".join(profile.get("conditions", [])))
            medications = st.text_input("当前用药（逗号分隔）", value="，".join(profile.get("medications", [])))
            saved = st.form_submit_button("保存")
        if saved:
            split = lambda value: [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
            try:
                _put("/api/profile", {"name": name, "age": age, "gender": gender, "allergies": split(allergies), "conditions": split(conditions), "medications": split(medications)})
                st.session_state.profile = None
                st.success("档案已保存")
                st.rerun()
            except (httpx.HTTPError, RuntimeError) as exc:
                st.error(f"保存失败：{exc}")


def _render_workspace() -> None:
    _render_sidebar()
    profile = _load_profile()
    st.markdown(
        '<div class="hero"><h1>智能医疗问诊</h1><p>由本地意图识别、Planning + ReAct、多代理与 MCP 医疗工具协同完成</p></div>',
        unsafe_allow_html=True,
    )
    chat_col, profile_col = st.columns([2.45, 1], gap="large")
    with chat_col:
        st.markdown('<span class="status"><i class="status-dot"></i>医疗 Agent 工作区</span>', unsafe_allow_html=True)
        if not st.session_state.messages:
            st.info("请描述症状、药品问题、检验结果或需要查询的医学指南。")
        for item in st.session_state.messages:
            with st.chat_message(item["role"], avatar="⚕" if item["role"] == "assistant" else None):
                st.markdown(item["content"])
        if st.session_state.agent_trace:
            with st.expander("查看 ReAct 工具执行轨迹"):
                st.json(st.session_state.agent_trace)
        prompt = st.chat_input("输入医疗问题；紧急症状请直接拨打 120")
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            answer = ""
            trace: list[dict[str, Any]] = []
            try:
                with st.chat_message("assistant", avatar="⚕"):
                    placeholder = st.empty()
                    with st.status("Agent 正在执行 ReAct 推理与医疗工具调用…", expanded=False):
                        for event, data in _sse_events(prompt):
                            if event == "session":
                                st.session_state.conversation_id = data.get("conversation_id")
                            elif event == "trace":
                                trace.append(data)
                            elif event == "delta":
                                answer += data.get("text", "")
                                placeholder.markdown(answer + "▌")
                    placeholder.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
                st.session_state.agent_trace = trace
                st.rerun()
            except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
                st.error(f"问诊服务调用失败：{exc}")
    with profile_col:
        _render_profile(profile)


_init_state()
_render_css()
if st.session_state.token:
    _render_workspace()
else:
    _render_login()
