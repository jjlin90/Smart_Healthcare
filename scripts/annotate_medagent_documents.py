"""Refresh Chinese reading annotations and repair affected Markdown anchors."""
from pathlib import Path
import json
import re
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.medagent_terminology import annotate_markdown, annotate_text
from scripts.preflight_git import _markdown_headings, _markdown_without_fences
from scripts.record_document_checks import latest_test_validation
from scripts.verify_delivery_evidence import default_desktop_pairs, repository_markdown_paths


def replace_paragraph(text, prefix, replacement):
    """A stable Chinese prefix survives annotations; missing targets are errors."""
    pattern = re.compile(r'^' + re.escape(prefix) + r'[^\n]*$', re.M)
    if len(pattern.findall(text)) != 1:
        raise ValueError(f'文档段落应唯一存在，不能静默跳过：{prefix}')
    return pattern.sub(lambda _: replacement, text)


def review_block(text, body):
    start, end = '<!-- BEGIN MEDAGENT DOC REVIEW -->', '<!-- END MEDAGENT DOC REVIEW -->'
    block = start + '\n## 本轮译注与文档一致性校正\n\n' + body + '\n' + end
    if start in text or end in text:
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError('文档校正记录标记不完整')
        return re.sub(re.escape(start) + '.*?' + re.escape(end), lambda _: block, text, flags=re.S)
    return text.rstrip() + '\n\n' + block + '\n'


def refresh():
    library = ROOT / 'docs/MedAgent_逐题适配'
    coverage = json.loads((library / 'coverage.json').read_text(encoding='utf-8'))
    count = {kind: sum(row['association'] == kind for row in coverage['examples'])
             for kind in ('question', 'chapter_overview', 'chapter_appendix')}
    placement = {kind: sum(row['placement'] == kind for row in coverage['examples'])
                 for kind in ('after_source_lead', 'beside_question')}
    engineering = ROOT / 'docs/MedAgent_文档对照与工程改进_2026-10-13.md'
    desktop_plan = default_desktop_pairs(ROOT, Path('desktop'))
    desktop_targets = len(desktop_plan)
    repository_docs = len(repository_markdown_paths(ROOT))
    desktop_docs = sum(source.suffix.lower() == '.md' for source, _ in desktop_plan)
    link_scope = f'仓库 {repository_docs} 份及桌面 {desktop_docs} 份，共 {repository_docs + desktop_docs} 份'
    text = engineering.read_text(encoding='utf-8')
    text = replace_paragraph(text, '核查日期：',
            "核查日期：2026-10-13。范围是本地保留的 14 份 .txt 原件，位于仓库根目录下 artifacts/medagent_upgrade_20261013/原始资料/；桌面同名 .md 为适配稿。"
            "包括主问题、追问、横向题表和连问模板。[面试话术主文](MedAgent_AI_多Agent项目话术.md#全文档逐题正文)完整保留原题及示例；[72 题理解导读](MedAgent_逐题适配/核心题理解导读.md)是可选理解入口，不替代题库，"
            "本次形成 1,260 个问答条目，其中 624 个章节主问题与追问、636 个题表单元与连问；重复位置分别计数，不表示 1,260 个独立主题。"
            "[逐题入口](MedAgent_逐题适配/README.md)和[覆盖清单](MedAgent_逐题适配/coverage.json)可查原件路径、原文行号和适配位置。原件不随克隆分发。")
    text = replace_paragraph(text, '先前逐题生成器跳过了围栏块，',
            f"先前逐题生成器跳过了围栏块，现已补回覆盖清单中的全部 {coverage['example_count']} 个示例。"
            f"归属：{count['question']} 个对应问答，{count['chapter_overview']} 个章首概览，{count['chapter_appendix']} 个独立章节附录；后两类 question_anchor 为 null。"
            "清单记录每块原文行号、原始与适配内容哈希及全文锚点。示例按院内患者记录与转诊协同解释，明确属于原理或扩展设计，不作为新增生产功能。")
    text = replace_paragraph(text, '136 个示例均只有一份正文：',
            f"136 个示例均只有一份正文：{placement['after_source_lead']} 个紧随保留的引导文字，{placement['beside_question']} 个位于对应问答后，{count['chapter_overview']} 个章首概览，{count['chapter_appendix']} 个独立章节附录。"
            f"内容比较后，{coverage['example_adaptation_counts']['unchanged']} 个保持原内容，{coverage['example_adaptation_counts']['tob_rewrite']} 个作院内场景改写。"
            "比较统一为 UTF-8/LF 并去除内容末尾空白；不宣称全部逐字还原。覆盖清单逐块保存 source_content_sha256、adapted_sha256 和 adaptation；原围栏整体的 source_sha256 另存。"
            "按原文分节确定归属，不仅依据最近题号。问答与示例保留锚点链接，扩展示例不代表生产功能。")
    text = replace_paragraph(text, '链接审计统一覆盖',
            f"链接审计统一覆盖{link_scope}，含完整面试主文、可选理解导读和分章资料。"
            "最新入口为 artifacts/medagent_upgrade_20261013/latest_link_audit.json，完整报告含执行起止时间、文件清单与哈希、检查器哈希、工作区状态和输入稳定性。"
            "独立 med_rag 项目及其稿件不在本轮交付与审计范围；指南工具调用不等于本仓库实现了 RAG。检查本地文件和锚点，外部 URL 只列举。旧审计均保留当时范围，不回填为本轮。"
            "使用 scripts/record_document_checks.py 的 --kind links 和 --desktop 即默认包含桌面工程对照，无需额外传入该文件。")
    text = replace_paragraph(text, '文档生成与审计修复范围',
            "文档生成与审计修复范围（2026-10-13）：该阶段只修改文档生成与审计代码。2026-10-14 已收紧临床专科职责：症状智能体保留症状辅助评估、风险提示、疾病信息和科室建议，不创建转诊草稿；医生在独立表单确认患者、科室和原因后，由 ReferralDraftService 经 MCP 执行参数绑定的草稿写入。前轮 479 项及 24.11 秒是历史记录，本轮验证另行记录。"
            "用户已主动删除备份，本轮直接覆盖原稿，不恢复或新增备份。")
    text = replace_paragraph(text, '桌面同步',
            f"桌面同步的当前默认清单包含逐题稿与覆盖清单、完整讲稿、理解指南、工程对照和 SVG/PNG 流程图，共 {desktop_targets} 个目标。"
            "共享简历 HTML/PDF 单独交付，不计入此清单；旧的 23 目标收据包含简历，仅作历史快照。"
            "独立 med_rag 稿件也不在当前项目交付范围。按用户要求直接更新桌面原稿，不新建备份；同步后逐项核验来源、目标字节及链接映射。"
            "覆盖清单中的原文哈希以首次读取的原始内容为准。")
    engineering_text = text
    guide = ROOT / 'docs/MedAgent_项目理解指南.md'
    text = guide.read_text(encoding='utf-8')
    text = replace_paragraph(text, '话术中的原文行号指向',
            "话术中的原文行号指向仓库根目录下 artifacts/medagent_upgrade_20261013/原始资料/*.txt 的本地原件；同名桌面 .md 为适配稿，原件不随克隆分发。"
            f"{count['question']} 个示例归属问答，{count['chapter_overview']} 个章首概览，{count['chapter_appendix']} 个章节附录。"
            f"完整链接审计范围为{link_scope}。独立 RAG 稿不纳入本轮。先查看 artifacts/medagent_upgrade_20261013/latest_link_audit.json，"
            "再打开 report 所指完整报告获取起止时间、清单和哈希。链接报告哈希对应其检查时的文档；代码测试的 input_sha256 是测试启动时的整工作区快照，生成文档后可不同。代码有效性核对 code_sha256，具体变化查看 recorded_input_differences；当前文档另看交付报告。HEAD 不代表未提交改动。")
    guide_text = text
    readme = ROOT / 'README.md'
    text = readme.read_text(encoding='utf-8')
    text = replace_paragraph(text, '2026-10-13 完整话术补充：',
            "2026-10-13 完整话术补充：本地保留的 14 份 .txt 原件的 1,260 处适配问答完整保存在[面试话术主文](docs/MedAgent_AI_多Agent项目话术.md#全文档逐题正文)；2026-10-15 重新组织原理、项目做法与例子，另提供[72 题理解导读](docs/MedAgent_逐题适配/核心题理解导读.md)，不缩减原题；"
            "原件位于 artifacts/medagent_upgrade_20261013/原始资料/，桌面同名 .md 为适配稿。新增统一时间预算传播和独立档案的内容 revision 条件更新；"
            "旧档案客户端须先 GET 取得 revision，再以 expected_revision 保存，缺版本返回 422、内容冲突返回 409。工作台已更新，无数据库迁移。")
    text = replace_paragraph(text, '2026-10-13 文档追溯复核：',
            "2026-10-13 文档追溯复核：原文行号指本机保留的 artifacts/medagent_upgrade_20261013/原始资料/*.txt，适配稿与原件分开；生成器读取已有原件时不重写，异源输入不同则拒绝覆盖。"
            f"{count['question']} 个示例属于问答，放置为 {placement['after_source_lead']} 个紧随引导语、{placement['beside_question']} 个置于问答后；另 {count['chapter_overview']} 个概览和 {count['chapter_appendix']} 个附录。桌面直接更新，不新建备份。")
    text = replace_paragraph(text, '2026-10-13 复核修复：',
            "2026-10-13 复核修复：全部 136 个示例仍收录于完整面试话术；流程图在 docs/MedAgent_项目流程图.svg。"
            f"生产委托与 Redis 围栏见工程对照。历史新增 16 项回归保留原始口径。当前链接审计为{link_scope}。独立 RAG 稿不计入本项目交付。"
            "同步收据为 artifacts/medagent_upgrade_20261013/desktop_sync_receipt.json；链接审计入口为 artifacts/medagent_upgrade_20261013/latest_link_audit.json。")
    annotation_note = ('英文译注刷新入口：scripts/annotate_medagent_documents.py；分章适配使用统一术语表，'
        '话术合并继承分章已有译注，刷新器不向主文和人工导读重复插入注释。'
        '新增的通俗说明由人工编写，需同时检查专业含义与中文表达；代码、路径及原始资料保留原样。'
        '主文由生成器重建，原题答案应编辑分章，项目开场应编辑导读，新增原理补充在生成器问答补充表维护。'
        '覆盖清单的 source 是适配映射键，original_local_path 才是本机原件路径。'
        '链接审计检查本地文件及锚点；Git 索引可分发检查另由预检执行，external_urls 仅列举外部网址。'
        '审计的 worktree_dirty 表示记录生成时的未提交改动，输入哈希识别实际检查内容。'
        '统计门禁核对约定统计段落的数值及完整性，接受无前导零的整数或标准三位分组的千分位格式；任意历史叙述仍需人工核对。'
        '当前 --delivery-pointer 会现场重算完整文档范围的链接；旧 --evidence-dir 的链接份数来自历史报告，不等于当前完整链接扫描。'
        '旧入口须提供 preflight.log、quick.log、anchor_drift.json、document_links.json、latest_desktop_receipt.json 和 desktop_manifest.json 六份输入，不能把任意 artifacts 目录传入。')
    text = (replace_paragraph(text, '英文译注刷新入口：', annotation_note)
            if '英文译注刷新入口：' in text else text.rstrip()+'\n\n'+annotation_note+'\n')
    validation = latest_test_validation()
    evidence = (f"本轮代码与依赖回归 {validation['test_counts']['passed']} 项通过；"
                f"记录器采集耗时 {validation['elapsed_seconds']} 秒，包含元数据采集、子进程启动、测试执行与临时目录清理；"
                f"pytest 自报的测试耗时见原始日志，两者不等同，也不作接口性能指标。执行标识 `{validation['run_id']}`。"
                if validation else "本轮代码回归尚无与当前代码匹配的成功记录；历史 479 项不得标为当前。")
    review = ("2026-10-13：在话术、14 份题库、理解指南、工程对照和流程图补充中文译注；"
              "术语、工具名、参数名逐项说明，代码与路径不改写。文件格式集中说明：.md 是 Markdown 文档，.txt 是纯文本原件，.json 是结构化数据文件，.svg 是矢量图，.png 是位图。"
              "已去除文件后缀的重复译注。17 个独立段落示例从上一题分离为章节附录，问答和示例总量不变。"
              "刷新器按唯一段落标记更新，缺失或重复会报错；保留历史验证口径。原件读取不再重写，mtime 不能证明首次捕获时间。\n\n"
              + evidence + "测试命令、代码指纹及原始结果见 artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json 指向的记录。"
              "该记录的 validation_scope 为 code_and_dependencies，code_sha256 核对代码与依赖；input_sha256 保留测试启动时的整工作区快照，后续文档再生成可使它与当前不同。recorded_input_differences 列出变化，不能把代码回归有效解释为所有文档哈希仍匹配。"
              f"该计数核验代码与依赖；生成后的当前文档另行核验 {coverage['question_count']:,} 处引用、{coverage['example_count']} 个示例内容和 {repository_docs + desktop_docs} 份本地链接，不能用代码回归代替文档核验。"
              "当前文档独立核验入口为 artifacts/medagent_upgrade_20261013/latest_document_delivery.json，所指报告逐项保存当前文档哈希与覆盖清单哈希。"
              "当前核验使用随仓库分发的 scripts/verify_delivery_evidence.py，传入 --delivery-pointer 和上述入口；该命令重算文档、原件、示例与桌面正文，并核对文档宣称的测试数。"
              "桌面写入与核验共用链接映射，默认同步清单已排除独立 RAG，不依赖 --medagent-only 才排除。"
              "本仓库本轮未修改独立 med_rag 项目的正文，其他会话对该项目或共享简历的更新不属于本轮交付状态。"
              "默认文档同步排除共享简历 HTML/PDF，简历另有需求时单独交付；不能把旧简历覆盖回桌面以消除历史收据漂移。"
              "artifacts/medagent_upgrade_20261013/med_rag_面试全解.md 与桌面同名稿属于前轮生成副本，不作为独立项目原件或当前交付。"
              "2026-10-14职责调整：九类临床辅助意图交三个专科，兼容标签“转诊协同”转为独立医生确认的转诊草稿业务入口，不增加第四个智能体。"
              "新增独立表单/API、确认参数摘要绑定、转诊写入审计与权限/去重/未知状态回归；症状专科和聊天均不能创建转诊。"
              "业务仍为院内 ToB；没有新增患者自助功能，没有真实医院写入。")
    # Keep the revised texts until all required paragraphs have matched.
    readme_text = review_block(text, review)
    targets = [readme, engineering, guide, ROOT / 'docs/MedAgent_AI_多Agent项目话术.md',
               *(path for path in library.rglob('*') if path.is_file() and path.suffix.lower() == '.md')]
    before = {path: path.read_text(encoding='utf-8-sig') for path in targets}
    before[readme] = readme_text
    before[guide] = review_block(guide_text, review)
    before[engineering] = review_block(engineering_text, review)
    edited_reading_paths = {ROOT / 'docs/MedAgent_AI_多Agent项目话术.md', library / '核心题理解导读.md'}
    # The edited reading guide explains terms once, in context / its glossary.
    # Repeated automatic glosses are kept in the technical reference only.
    after = {path: text if path in edited_reading_paths else annotate_markdown(text) for path, text in before.items()}
    anchor_maps = {}
    for path, original in before.items():
        previous = subprocess.run(['git', 'show', 'HEAD:' + path.relative_to(ROOT).as_posix()],
                                  cwd=ROOT, capture_output=True).stdout.decode('utf-8', errors='replace')
        mapping = {}
        actual_anchors = _markdown_headings(after[path])[1]
        for title in _markdown_headings(original + '\n' + previous)[0]:
            old = next(iter(_markdown_headings('## ' + title)[1]))
            new = next(iter(_markdown_headings('## ' + annotate_text(title))[1]))
            if old != new and new in actual_anchors:
                mapping[old] = new
        anchor_maps[path.resolve()] = mapping
    for path, text in after.items():
        def link(match):
            label, raw = match.groups()
            dest, mark, anchor = unquote(raw.strip('<>')).partition('#')
            if not mark or re.match(r'^https?://', dest):
                return match[0]
            target = (path.parent / dest).resolve() if dest else path.resolve()
            replacement = anchor_maps.get(target, {}).get(anchor)
            return f'[{label}]({raw.replace(anchor, replacement)})' if replacement else match[0]
        parts = re.split(r'(^```[^\n]*\n.*?^```[ \t]*$)', text, flags=re.M | re.S)
        text = ''.join(part if part.startswith('```') else re.sub(
            r'\[([^\]\n]+)\]\((<[^>\n]+>|[^)\s]+)\)', link, part) for part in parts)
        path.write_text(text, encoding='utf-8', newline='\n')
    print(json.dumps({'annotated_documents': len(targets), 'example_associations': count}, ensure_ascii=False))


if __name__ == '__main__':
    refresh()
