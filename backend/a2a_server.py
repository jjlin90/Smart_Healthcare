"""Run one protocol-compliant A2A medical sub-agent.

Examples:
  python -m backend.a2a_server --agent symptom --port 8011
  python -m backend.a2a_server --agent drug --port 8012
  python -m backend.a2a_server --agent guide --port 8013
"""

import argparse
import asyncio
import json

from python_a2a import A2AServer, AgentCard, AgentSkill, Message, MessageRole, TextContent, run_server

from backend.agents import AGENT_TOOLS, MCPToolAgent

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

    def handle_message(self, message: Message) -> Message:
        try:
            payload = json.loads(message.content.text)
            result = asyncio.run(self.runtime.run(task=payload["task"], patient_id=payload["patient_id"], profile=payload.get("profile", {}), history=payload.get("history", [])))
            text = json.dumps({"success": True, **result}, ensure_ascii=False)
        except Exception as exc:
            text = json.dumps({
                "success": False,
                "error_code": "AGENT_EXECUTION_FAILED",
                "retryable": False,
                "error": "专科 Agent 执行失败，请联系管理员检查依赖服务",
                "trace": [],
            }, ensure_ascii=False)
        return Message(content=TextContent(text=text), role=MessageRole.AGENT, parent_message_id=message.message_id, conversation_id=message.conversation_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    name, description = AGENTS[args.agent]
    server = MedicalA2AServer(name, description, f"http://{args.host}:{args.port}")
    run_server(server, host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
