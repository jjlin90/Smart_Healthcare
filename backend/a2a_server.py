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
    "symptom": ("SymptomAgent", "症状分析、辅助分诊、健康咨询与挂号指引"),
    "drug": ("DrugAgent", "药品说明书、禁忌症、相互作用与替代药查询"),
    "guide": ("GuideAgent", "临床指南、检验报告与标准诊疗路径检索"),
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
                "retryable": True,
                "error": str(exc),
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
