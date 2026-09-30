"""Run one protocol-compliant A2A medical sub-agent.

Examples:
  python -m backend.a2a_server --agent symptom --port 8011
  python -m backend.a2a_server --agent drug --port 8012
  python -m backend.a2a_server --agent guide --port 8013
"""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from flask import g, jsonify, request
from jose import JWTError
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.wsgi import WSGIMiddleware
import uvicorn

from python_a2a import A2AServer, AgentCard, AgentSkill, Message, MessageRole, TextContent

from backend.agents import AGENT_TOOLS, MCPToolAgent
from backend.a2a_context import HISTORY_BYTES, history_size
from backend.access import active_staff, require_patient
from backend.config import get_settings
from backend.database import SessionLocal
from backend.service_auth import delegated_context, verify_delegation, validate_service_secret
from python_a2a.server.http import create_flask_app

AGENTS = {
    "symptom": ("SymptomAgent", "院内症状评估、辅助分诊与转诊协同"),
    "drug": ("DrugAgent", "药品信息、用药审核、禁忌症、相互作用与替代药查询"),
    "guide": ("GuideAgent", "临床指南、检验与报告辅助解读、随访管理及临床知识查询"),
}


class MedicalA2AServer(A2AServer):
    def __init__(self, agent_name: str, description: str, url: str) -> None:
        skills = [AgentSkill(name=tool, description=f"通过 MCP 调用 {tool}", tags=["medical", "mcp"]) for tool in AGENT_TOOLS[agent_name]]
        super().__init__(agent_card=AgentCard(name=agent_name, description=description, url=url, version="1.0.0", skills=skills))
        self.runtime = MCPToolAgent(agent_name)
        self.agent_name = agent_name
        self.runtime_loop = None

    async def execute(self, payload: dict, claims: dict) -> dict:
        task = SpecialistRequest.model_validate(payload)
        if claims["agent"] != self.agent_name or task.patient_id != claims["patient_id"]:
            raise ValueError("Delegation scope mismatch")
        async with SessionLocal() as db:
            user = await active_staff(db, claims["staff_id"])
            profile = await require_patient(db, user, task.patient_id)
            trusted_profile = {
                "patient_id": profile.patient_id, "name": profile.name, "username": user.username,
                "allergies": json.loads(profile.allergy_history or "[]"),
                "conditions": json.loads(profile.past_medical_history or "[]"),
                "medications": json.loads(profile.current_medications or "[]"),
            }
        with delegated_context(claims):
            async with asyncio.timeout(get_settings().request_timeout_seconds):
                return await self.runtime.run(task.task, task.patient_id, trusted_profile, task.history)

    def handle_message(self, message: Message) -> Message:
        try:
            payload = json.loads(message.content.text)
            if self.runtime_loop is None:
                raise RuntimeError("A2A runtime is not started")
            claims = g.delegation
            pending = asyncio.run_coroutine_threadsafe(self.execute(payload, claims), self.runtime_loop)
            try:
                result = pending.result(timeout=get_settings().request_timeout_seconds + 5)
            except TimeoutError:
                pending.cancel()
                raise
            text = json.dumps({"success": True, **result}, ensure_ascii=False)
        except Exception:
            text = json.dumps({
                "success": False,
                "error_code": "AGENT_EXECUTION_FAILED",
                "retryable": False,
                "error": "专科 Agent 执行失败，请联系管理员检查依赖服务",
                "trace": [],
            }, ensure_ascii=False)
        return Message(content=TextContent(text=text), role=MessageRole.AGENT, parent_message_id=message.message_id, conversation_id=message.conversation_id)


class SpecialistRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    patient_id: str = Field(min_length=1, max_length=64)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)

    @field_validator("history")
    @classmethod
    def validate_history_size(cls, value):
        if history_size(value) > HISTORY_BYTES:
            raise ValueError("Conversation history exceeds byte budget")
        return value


def create_app(server: MedicalA2AServer) -> FastAPI:
    flask_app = create_flask_app(server)
    flask_app.config["MAX_CONTENT_LENGTH"] = 128 * 1024

    @flask_app.before_request
    def authenticate():
        try:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise JWTError("Missing service identity")
            g.delegation = verify_delegation(header[7:], f"medagent-a2a:{server.agent_name}")
        except (JWTError, ValueError, RuntimeError):
            return jsonify({"error": "service_authentication_required"}), 401

    @asynccontextmanager
    async def lifespan(app):
        validate_service_secret()
        server.runtime_loop = asyncio.get_running_loop()
        try:
            yield
        finally:
            server.runtime_loop = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    from prometheus_client import make_asgi_app
    app.mount("/metrics", make_asgi_app())
    app.mount("/", WSGIMiddleware(flask_app))
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    name, description = AGENTS[args.agent]
    server = MedicalA2AServer(name, description, f"http://{args.host}:{args.port}")
    uvicorn.run(create_app(server), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
