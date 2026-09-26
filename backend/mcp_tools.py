"""Production FastMCP server exposing the 14 tools from the project brief.

No medical-data fallback is provided: external tools call configured hospital
systems and internal tools operate on the configured database.
"""

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
from fastmcp import FastMCP
from sqlalchemy import select
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential
from starlette.responses import JSONResponse

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import AuditLog, Consultation, PatientProfile, Referral
from backend.resilience import get_breaker
from backend.mcp_security import DelegationVerifier, PatientAuthorization

mcp = FastMCP("MedAgent Medical MCP Server", instructions="医疗工具返回仅供医生参考。", auth=DelegationVerifier(), middleware=[PatientAuthorization()], mask_error_details=True)

EXTERNAL_TOOL_NAMES = [
    "analyze_symptoms", "suggest_department", "get_disease_info",
    "query_drug_info", "check_drug_interaction", "check_contraindications",
    "query_drug_alternatives", "search_guidelines", "interpret_lab_results",
    "get_treatment_protocol", "generate_referral",
]
INTERNAL_TOOL_NAMES = ["save_patient_history", "load_patient_history", "save_medical_record"]
ALL_TOOL_NAMES = EXTERNAL_TOOL_NAMES + INTERNAL_TOOL_NAMES


@mcp.custom_route("/health/live", methods=["GET"])
async def liveness(request):
    return JSONResponse({"status": "alive"})


class HospitalAPIError(RuntimeError):
    pass


def _auth_headers(body: bytes) -> dict[str, str]:
    settings = get_settings()
    settings.require("hospital_app_key", "hospital_app_secret")
    timestamp = str(int(time.time()))
    signature = hmac.new(settings.hospital_app_secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-App-Key": settings.hospital_app_key, "X-Timestamp": timestamp, "X-Signature": signature, "Content-Type": "application/json"}


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {429, 500, 502, 503, 504}
    )


async def _hospital_post(base_url: str, path: str, payload: dict[str, Any], *, retry_safe: bool = True) -> dict[str, Any]:
    if not base_url:
        raise RuntimeError(f"医院接口未配置：{path}")
    async def request() -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        async with httpx.AsyncClient(timeout=get_settings().external_request_timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/{path.lstrip('/')}", content=body, headers=_auth_headers(body))
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise HospitalAPIError("医院接口返回格式不是 JSON 对象")
            if data.get("success") is False:
                raise HospitalAPIError(str(data.get("message") or "医院接口返回失败"))
            result = data.get("data", data)
            if not isinstance(result, dict):
                raise HospitalAPIError("医院接口 data 字段必须为 JSON 对象")
            return result

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3 if retry_safe else 1),
        wait=wait_exponential(multiplier=0.3, min=0.3, max=2),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    ):
        with attempt:
            return await get_breaker(f"hospital:{base_url}").call(request, is_failure=_is_transient)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def analyze_symptoms(symptoms: list[str], duration: str, severity: str | None = None) -> dict[str, Any]:
    """分析标准化症状并返回可能方向和严重程度，不得作为疾病诊断。"""
    return await _hospital_post(get_settings().his_api_base_url, "/v1/symptoms/analyze", {"symptoms": symptoms, "duration": duration, "severity": severity})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def suggest_department(symptoms: list[str]) -> dict[str, Any]:
    """根据症状从医院真实科室目录中推荐科室。"""
    return await _hospital_post(get_settings().his_api_base_url, "/v1/departments/suggest", {"symptoms": symptoms})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def get_disease_info(disease_name: str) -> dict[str, Any]:
    """查询医院医学知识库中的疾病资料。"""
    return await _hospital_post(get_settings().guideline_api_base_url, "/v1/diseases/query", {"disease_name": disease_name})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def query_drug_info(drug_name: str) -> dict[str, Any]:
    """查询医院药品库中的说明书、适应症、禁忌症和不良反应。"""
    return await _hospital_post(get_settings().drug_api_base_url, "/v1/drugs/query", {"drug_name": drug_name, "include_interactions": True})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def check_drug_interaction(drug_list: list[str]) -> dict[str, Any]:
    """对完整药品列表进行两两相互作用检查。"""
    return await _hospital_post(get_settings().drug_api_base_url, "/v1/drugs/interactions", {"drug_list": drug_list})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def check_contraindications(drug_name: str, patient_condition: dict[str, Any]) -> dict[str, Any]:
    """结合已授权患者状况检查真实药品禁忌症。"""
    return await _hospital_post(get_settings().drug_api_base_url, "/v1/drugs/contraindications", {"drug_name": drug_name, "patient_condition": patient_condition})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def query_drug_alternatives(drug_name: str, reason: str | None = None) -> dict[str, Any]:
    """从医院药品目录查询可供医生选择的替代药品。"""
    return await _hospital_post(get_settings().drug_api_base_url, "/v1/drugs/alternatives", {"drug_name": drug_name, "reason": reason})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_guidelines(disease: str, keyword: str | None = None) -> dict[str, Any]:
    """检索临床指南并返回发布日期、更新时间和版本。"""
    return await _hospital_post(get_settings().guideline_api_base_url, "/v1/guidelines/search", {"disease": disease, "keyword": keyword})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def interpret_lab_results(test_results: dict[str, Any]) -> dict[str, Any]:
    """调用 LIS 解释检验指标并标注参考范围和异常项。"""
    return await _hospital_post(get_settings().lis_api_base_url, "/v1/lab-results/interpret", {"test_results": test_results})


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def get_treatment_protocol(disease: str) -> dict[str, Any]:
    """查询医院审核过的标准诊疗路径，仅供医生参考。"""
    return await _hospital_post(get_settings().guideline_api_base_url, "/v1/protocols/query", {"disease": disease})


@mcp.tool()
async def save_patient_history(patient_id: str, history_data: dict[str, Any]) -> dict[str, Any]:
    """按 patient_id 将患者画像字段保存至院内数据库。"""
    from backend.schemas import HistoryUpdate
    history_data = HistoryUpdate.model_validate(history_data).model_dump(exclude_none=True)
    if not history_data:
        raise ValueError("必须提供至少一项病史字段")
    async with SessionLocal() as db:
        profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
        if not profile:
            raise ValueError("患者不存在或未授权")
        mapping = {"allergies": "allergy_history", "conditions": "past_medical_history", "medications": "current_medications"}
        changed: list[str] = []
        for source, target in mapping.items():
            if source in history_data:
                setattr(profile, target, json.dumps(history_data[source], ensure_ascii=False)); changed.append(source)
        db.add(AuditLog(patient_id=patient_id, action="save_patient_history", detail=json.dumps({"changed": changed}, ensure_ascii=False)))
        await db.commit()
        return {"saved": True, "patient_id": patient_id, "changed_fields": changed}


@mcp.tool(annotations={"readOnlyHint": True})
async def load_patient_history(patient_id: str) -> dict[str, Any]:
    """按 patient_id 加载过敏史、既往病史和当前用药。"""
    async with SessionLocal() as db:
        profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
        if not profile:
            raise ValueError("患者不存在或未授权")
        return {"patient_id": patient_id, "age": profile.age, "gender": profile.gender, "allergies": json.loads(profile.allergy_history or "[]"), "conditions": json.loads(profile.past_medical_history or "[]"), "medications": json.loads(profile.current_medications or "[]")}


@mcp.tool()
async def save_medical_record(patient_id: str, record_data: dict[str, Any]) -> dict[str, Any]:
    """保存本次真实 Agent 院内临床辅助记录。"""
    from backend.schemas import RecordWrite
    record_data = RecordWrite.model_validate(record_data).model_dump()
    async with SessionLocal() as db:
        db.add(Consultation(patient_id=patient_id, title=str(record_data.get("title") or "临床辅助任务")[:128], symptoms=json.dumps(record_data.get("input", {}), ensure_ascii=False), response=str(record_data.get("response") or ""), intent=str(record_data.get("intent") or "未知")[:32], suggested_department=record_data.get("department")))
        await db.commit()
        return {"saved": True, "patient_id": patient_id}


@mcp.tool()
async def generate_referral(patient_id: str, department: str, reason: str) -> dict[str, Any]:
    """在 HIS 生成待医生确认的转诊单并记录编号。"""
    if not department.strip() or len(department) > 64 or not reason.strip() or len(reason) > 4000:
        raise ValueError("转诊科室或原因无效")
    result = await _hospital_post(get_settings().his_api_base_url, "/v1/referrals", {"patient_id": patient_id, "department": department, "reason": reason, "require_confirmation": True}, retry_safe=False)
    if not result.get("referral_id") or result.get("status") not in {"pending_confirmation", "draft", "待医生确认"}:
        raise ValueError("HIS 未确认转诊草稿编号或待确认状态，请核对下游结果")
    async with SessionLocal() as db:
        referral = Referral(patient_id=patient_id, department=department, reason=reason, external_id=str(result.get("referral_id", "")), status="待医生确认")
        db.add(referral); await db.commit(); await db.refresh(referral)
        return {"referral_id": referral.external_id or str(referral.id), "status": referral.status, "department": department}


if __name__ == "__main__":
    # The banner performs an optional version-cache write. Disabling it keeps
    # the service runnable in locked-down hospital hosts without changing MCP.
    settings = get_settings()
    from backend.service_auth import validate_service_secret
    validate_service_secret()
    mcp.run(
        transport="http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path="/mcp",
        show_banner=False,
        stateless_http=True,
    )
