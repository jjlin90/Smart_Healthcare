"""Put every adapted answer in the single project interview document.

The independent chapter files remain useful sources, but the combined document
is self-contained. Every source question receives a unique local heading.
"""
from pathlib import Path
import json
import re
import os
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.medagent_terminology import annotate_markdown
from scripts.record_document_checks import latest_test_validation
LIBRARY = ROOT / "docs/MedAgent_逐题适配"
BOOK = ROOT / "docs/MedAgent_AI_多Agent项目话术.md"
START = "<!-- BEGIN MEDAGENT FULL QUESTION LIBRARY -->"
END = "<!-- END MEDAGENT FULL QUESTION LIBRARY -->"


def example_summary(coverage):
    counts = {kind: sum(row['association'] == kind for row in coverage['examples'])
              for kind in ('question', 'chapter_overview', 'chapter_appendix')}
    return (f"{counts['question']} 个示例随对应问答保留，{counts['chapter_overview']} 个章首示例单列概览，"
            f"{counts['chapter_appendix']} 个独立段落示例单列章节附录")


def merge():
    coverage_path = LIBRARY / "coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    chapters = [row["name"] for row in coverage["source_files"]]
    codes = {name: name[:2] if re.match(r"\d\d-", name) else "cross" for name in chapters}
    anchors = {(q["output"], q["anchor"]):f"full-{codes[q['output']]}-{q['anchor']}" for q in coverage["questions"]}
    anchors.update({(q["output"], q["anchor"]): f"full-{codes[q['output']]}-{q['anchor']}"
                    for q in coverage.get("examples", [])})
    base = BOOK.read_text(encoding="utf-8-sig").split(START)[0].rstrip()
    # Replace the old navigation-only appendix with the complete answers.
    base = base.split("## 2026-10-13 全文档适配与执行机制更新")[0].rstrip()
    base = re.sub(r"最近核查：\d{4}-\d{2}-\d{2}", "最近核查：2026-10-14", base, count=1)
    # Rebuild the repeated summary too; it predates the generated appendix.
    base = re.sub(r'(?m)^全文保留 [\d,]+ 处问答与 \d+ 个图、伪代码和结构示例。[^\n]*$',
                  f"全文保留 {coverage['question_count']:,} 处问答与 {coverage['example_count']} 个图、伪代码和结构示例。"
                  + example_summary(coverage) + "；引用指向本地保留的 .txt 原件。示例标明原理或扩展设计，不冒充现有功能。", base)
    validation = latest_test_validation()
    current = (f"最新代码与依赖回归 {validation['test_counts']['passed']} 项通过，记录见 [验证](../README.md#验证)；生成文档另行核验引用、内容哈希和本地链接。"
               if validation else "完整工程回归结果见 [验证](../README.md#验证)；479 项为前轮历史记录。")
    base = re.sub(r'(?m)^生产签名委托必带任务、[^\n]*$',
                  "生产签名委托必带任务、会话、计划版本、步骤和绝对期限，缺项拒绝；MCP 必须经 Redis 检查活跃版本。"
                  "开发环境无 Redis 时没有跨进程围栏。专科检查授权与原文摘要，协调器核对返回任务版本；最终保存后发送 done。"
                  + current + "医疗正确性仍须由院内人员复核。", base)
    counts = {kind: sum(row['association'] == kind for row in coverage['examples'])
              for kind in ('question', 'chapter_overview', 'chapter_appendix')}
    intro = ("本文件正文已完整纳入本地 14 份原件的 1,260 处适配问答（包括重复问法）及 136 个图、伪代码和结构示例，无需打开分册才能读答案。原件保存在仓库根目录下 artifacts/medagent_upgrade_20261013/原始资料/*.txt，不随克隆分发；桌面同名 .md 为适配稿。原项目介绍之后是[完整逐题正文](#全文档逐题正文)；"
             f"{counts['question']} 个示例随对应问答保留，{counts['chapter_overview']} 个首题之前的示例单列章节概览，{counts['chapter_appendix']} 个独立段落示例归属章节附录。"
             "引用指向本地保留的 .txt 原件，区分当前实现、原理与扩展方案，业务范围固定为院内 ToB。英文术语、工具名和参数名均附中文译注；代码内保留原标识符，译注列在示例之后。")
    base = re.sub(r"\n本文件正文已完整纳入[^\n]*\n", "\n", base)
    first, remainder = base.split("\n", 1)
    base = first + "\n\n" + intro + "\n\n" + remainder.lstrip("\n")
    appendix = [START, "## 全文档逐题正文", "", "以下为全部题目和答案。保留来源文件、原文行号及重复问法；主问题与追问的项目回答已直接写在本文件，不以导航代替正文。", "", "原始资料编号为 00—08、10—13，加一份横向题表，共 14 份；来源本身没有 09 号文件，跳号不是遗漏。", "", "### 全文章节导航", ""]
    for name in chapters:
        count = sum(q["output"] == name for q in coverage["questions"])
        appendix.append(f"- [{Path(name).stem}：{count} 处問答](#full-{codes[name]})".replace("問", "问"))
    appendix.append("")
    def rebase(text, name):
        def link(match):
            label, raw = match.groups()
            raw = raw.strip("<>")
            if re.match(r"(?:https?://|[A-Za-z]:/)", raw): return match.group(0)
            path, marker, fragment = unquote(raw).partition("#")
            if (path, fragment) in anchors:
                return f"[{label}](#{anchors[(path,fragment)]})"
            if not path and (name, fragment) in anchors:
                return f"[{label}](#{anchors[(name,fragment)]})"
            target = (LIBRARY / path).resolve()
            if target.is_relative_to(ROOT):
                relative = Path(os.path.relpath(target, BOOK.parent)).as_posix()
                dest = relative + marker + fragment
                return f"[{label}](<{dest}>)" if " " in dest else f"[{label}]({dest})"
            return match.group(0)
        blocks = re.split(r"(^```[^\n]*\n.*?^```[ \t]*$)", text, flags=re.M | re.S)
        return ''.join(block if block.startswith('```') else re.sub(
            r"\[([^\]\n]+)\]\((<[^>\n]+>|[^)\s]+)\)", link, block) for block in blocks)
    for name in chapters:
        chapter = (LIBRARY/name).read_text(encoding="utf-8-sig")
        # All content after the first question belongs to the complete body.
        body = chapter[chapter.index("## q-"):]
        body = re.sub(r"(?m)^### ", "#### ", body)
        body = re.sub(r"(?m)^## (q-\d+)[ \t]*$", lambda m: "### " + anchors[(name,m[1])], body)
        body = re.sub(r"(?m)^#### (example-\d+)[ \t]*$", lambda m: "#### " + anchors[(name,m[1])], body)
        body = body.replace("## 原文图与结构示例（ToB适配）", "### 原文图与结构示例（ToB适配）")
        body = body.replace("## 章节概览示例（无对应问答）", "### 章节概览示例（无对应问答）")
        body = body.replace("## 章节附录示例（无对应问答）", "### 章节附录示例（无对应问答）")
        appendix += [f"## full-{codes[name]}", "", f"**{Path(name).stem}：项目适配问答**", "", rebase(body,name).rstrip(), ""]
    appendix.append(END)
    result = annotate_markdown(base + "\n\n" + "\n".join(appendix) + "\n")
    for q in coverage["questions"]:
        anchor = anchors[(q["output"],q["anchor"])]
        if result.count("### " + anchor + "\n") != 1:
            raise ValueError("Question missing or duplicated: " + anchor)
        q["book"] = BOOK.name
        q["book_anchor"] = anchor
    for example in coverage.get("examples", []):
        anchor = anchors[(example["output"], example["anchor"])]
        if result.count("#### " + anchor + "\n") != 1:
            raise ValueError("Example missing or duplicated: " + anchor)
        example["book_anchor"] = anchor
    BOOK.write_text(result, encoding="utf-8", newline="\n")
    coverage_path.write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"chapters":len(chapters),"questions":len(coverage['questions']),"book_lines":len(result.splitlines()),"book":str(BOOK)},ensure_ascii=False))


if __name__ == "__main__":
    merge()
