"""Task-scoped conversation selection; patient identity always comes from auth.

History is supporting context, never a source of authorization. Ambiguous edits
are clarified instead of silently copying the previous clinical request.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4


@dataclass
class TurnContext:
    task_id: str
    plan_version: int
    mode: Literal["new", "continue", "modify", "unclear"]
    message: str
    history: list[dict[str, str]]
    clarification: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "plan_version": self.plan_version,
                "mode": self.mode, "question": self.message}


def select_turn(message: str, checkpoint: dict[str, Any] | None,
                history: list[dict[str, str]], mode: str = "auto") -> TurnContext:
    """Only reuse a bounded pending question, with explicit continuation rules.

    Explicit task mode is also available to the staff workbench. An ordinary
    completed turn is not treated as a pending task. Failed writes are never
    resumed here: operation reconciliation is a separate, authorized action.
    """
    previous = (checkpoint or {}).get("task_context") or {}
    pending = (checkpoint or {}).get("intent") == "需要澄清"
    question = previous.get("question", "")
    if pending and not question and len(history) >= 2:
        question = history[-2].get("content", "")[:2000]
    active = bool(question and previous.get("task_id")) or bool(pending and question)
    task_id = str(previous.get("task_id") or uuid4().hex)
    version = int(previous.get("plan_version", 1))
    new = bool(re.search(r"^(?:新任务|换个问题|另一个问题|另外|重新开始|取消(?:上个|之前|原来)(?:任务|问题))", message.strip()))
    standalone = bool(re.search(r"(?:查(?:询|一下)?|检索|解读|评估|审核|分析|制定|生成).{0,30}(?:说明书|指南|检验|报告|用药|症状|随访|转诊)", message))
    standalone = standalone or bool(re.search(r"^(?:请|帮我|麻烦|请帮我)?(?:查|检索|解读|审核|评估|分析|制定|生成|看看).{2,}", message.strip()))
    out_of_scope = bool(re.search(r"挂号|缴费|退费|(?:帮我|请|想)(?:预约|写代码|订票|退款)", message))
    edit = bool(re.search(r"^(?:不对|不是|改为|改成|换成|纠正|更正|我问的不是)", message.strip()))
    if mode == "new" or new or out_of_scope or (mode == "auto" and standalone and not edit):
        return TurnContext(uuid4().hex, 1, "new", message, [])
    if mode in {"continue", "modify"} and not active:
        return TurnContext(uuid4().hex, 1, "unclear", message, [],
                           "没有可续接的院内任务，请重新说明本次工作目标。")
    if mode == "modify" or (mode == "auto" and edit):
        # A partial correction cannot safely be concatenated with an old value.
        if not standalone:
            return TurnContext(task_id, version + 1, "unclear", message, [],
                               "请完整说明更正后的院内任务，例如‘查询阿司匹林说明书’；本次暂停旧计划和写动作。")
        return TurnContext(task_id, version + 1, "modify", message, [])
    if active and (mode == "continue" or pending):
        # Only an answer to a pending clarification is automatically inherited.
        if re.fullmatch(r"(?:这个|那个|它|好的|嗯|继续|随便|都行|改成别的)[？?。！!\s]*", message.strip()):
            return TurnContext(task_id, version, "unclear", message, [],
                               "请给出具体补充信息或明确说明新任务，不能仅凭指代继续执行。")
        combined = f"前次院内任务：{question[:2000]}\n本次补充（以本次明确输入为准）：{message}"
        # No assistant-generated history is promoted into clinical facts.
        return TurnContext(task_id, version, "continue", combined, [])
    return TurnContext(uuid4().hex, 1, "new", message, [])
