"""Put every adapted answer in the single project interview document.

The independent chapter files remain useful sources, but the combined document
is self-contained. Every source question receives a unique local heading.
"""
from pathlib import Path
import json
import re
import os
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "docs/MedAgent_逐题适配"
BOOK = ROOT / "docs/MedAgent_AI_多Agent项目话术.md"
START = "<!-- BEGIN MEDAGENT FULL QUESTION LIBRARY -->"
END = "<!-- END MEDAGENT FULL QUESTION LIBRARY -->"


def merge():
    coverage_path = LIBRARY / "coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    chapters = [row["name"] for row in coverage["source_files"]]
    codes = {name: name[:2] if re.match(r"\d\d-", name) else "cross" for name in chapters}
    anchors = {(q["output"], q["anchor"]):f"full-{codes[q['output']]}-{q['anchor']}" for q in coverage["questions"]}
    base = BOOK.read_text(encoding="utf-8-sig").split(START)[0].rstrip()
    # Replace the old navigation-only appendix with the complete answers.
    base = base.split("## 2026-10-13 全文档适配与执行机制更新")[0].rstrip()
    base = re.sub(r"最近核查：\d{4}-\d{2}-\d{2}", "最近核查：2026-10-13", base, count=1)
    intro = "本文件正文已完整纳入桌面 14 份资料的 1,260 处适配问答（包括重复问法），无需打开分册才能读答案。原项目介绍之后是[完整逐题正文](#全文档逐题正文)；答案区分当前实现、原理与扩展方案，业务范围固定为院内 ToB。"
    base = re.sub(r"\n本文件正文已完整纳入[^\n]*\n", "\n", base)
    first, remainder = base.split("\n", 1)
    base = first + "\n\n" + intro + "\n\n" + remainder.lstrip("\n")
    appendix = [START, "## 全文档逐题正文", "", "以下为全部题目和答案。保留来源文件、原文行号及重复问法；主问题与追问的项目回答已直接写在本文件，不以导航代替正文。", "", "### 全文章节导航", ""]
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
        return re.sub(r"\[([^\]\n]+)\]\((<[^>\n]+>|[^)\s]+)\)", link, text)
    for name in chapters:
        chapter = (LIBRARY/name).read_text(encoding="utf-8-sig")
        # All content after the first question belongs to the complete body.
        body = chapter[chapter.index("## q-"):]
        body = re.sub(r"(?m)^### ", "#### ", body)
        body = re.sub(r"(?m)^## (q-\d+)[ \t]*$", lambda m: "### " + anchors[(name,m[1])], body)
        appendix += [f"## full-{codes[name]}", "", f"**{Path(name).stem}：项目适配问答**", "", rebase(body,name).rstrip(), ""]
    appendix.append(END)
    result = base + "\n\n" + "\n".join(appendix) + "\n"
    for q in coverage["questions"]:
        anchor = anchors[(q["output"],q["anchor"])]
        if result.count("### " + anchor + "\n") != 1:
            raise ValueError("Question missing or duplicated: " + anchor)
        q["book"] = BOOK.name
        q["book_anchor"] = anchor
    BOOK.write_text(result, encoding="utf-8", newline="\n")
    coverage_path.write_text(json.dumps(coverage,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"chapters":len(chapters),"questions":len(coverage['questions']),"book_lines":len(result.splitlines()),"book":str(BOOK)},ensure_ascii=False))


if __name__ == "__main__":
    merge()
