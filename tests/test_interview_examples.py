import pytest

from scripts.adapt_medagent_interviews import source_examples, append_examples


@pytest.mark.parametrize('suffix', ['.md', '.MD', '.txt', '.TXT'])
def test_original_discovery_accepts_case_insensitive_suffixes(tmp_path, monkeypatch, suffix, case_sensitive_markdown_globs):
    from scripts import adapt_medagent_interviews as adapter
    paths = [tmp_path/f'{index:02d}-source{suffix}' for index in range(14)]
    for path in paths:
        path.write_text('# Original\n', encoding='utf-8')
    monkeypatch.setattr(adapter, 'PRESERVED', tmp_path)
    assert adapter.source_paths(tmp_path) == paths
    assert adapter.default_source() == tmp_path
    assert all(path.read_text(encoding='utf-8') == '# Original\n' for path in paths)


def test_original_discovery_prefers_markdown_and_rejects_partial_inventory(tmp_path, case_sensitive_markdown_globs):
    from scripts.adapt_medagent_interviews import source_paths
    markdown = [tmp_path/f'{index:02d}-source.MD' for index in range(14)]
    for path in markdown:
        path.write_text('# Markdown\n', encoding='utf-8')
        path.with_suffix('.txt').write_text('# Text\n', encoding='utf-8')
    (tmp_path/'ignored.json').write_text('{}', encoding='utf-8')
    assert source_paths(tmp_path) == markdown
    markdown[-1].unlink()
    with pytest.raises(ValueError, match='实际13份'):
        source_paths(tmp_path)


def test_all_fenced_examples_survive_including_before_first_question(tmp_path):
    source = tmp_path / "00-overview.txt"
    source.write_text('# 概览\n```mermaid\nflowchart TD\n A[查订单] --> B[退款评估]\n```\n'
                      '## Q1｜怎么调度？\n典型结构：\n```json\n{"order_id": "12345"}\n```\n', encoding="utf-8")
    rows = source_examples(source)
    assert len(rows) == 2 and [r['language'] for r in rows] == ['mermaid', 'json']
    assert rows[0]['line'] == 2 and rows[1]['line'] == 8
    assert '订单' not in rows[0]['content'] and '退款' not in rows[0]['content']
    assert 'patient_id' in rows[1]['content']
    assert all(row['adaptation'] == 'tob_rewrite' for row in rows)
    assert rows[1]['source_content_sha256'] != rows[1]['adapted_sha256']
    body = ['## q-001', '', '典型结构：', '后续解释']
    examples = append_examples(body, source, [{'line': 6, 'anchor': 'q-001'}])
    assert examples[0]['question_anchor'] is None
    assert examples[1]['question_anchor'] == 'q-001'
    assert examples[0]['association'] == 'chapter_overview'
    assert examples[1]['placement'] == 'after_source_lead'
    assert examples[1]['original_local_path'].endswith('/00-overview.txt')
    rendered = '\n'.join(body)
    assert '不表示代码已落地' in rendered
    assert rendered.index('典型结构：') < rendered.index('```json') < rendered.index('后续解释')
    assert '当前同名 .md 是适配稿' in rendered
    assert '\n'.join(body).count('```') == 4

    source.write_text(source.read_text(encoding='utf-8') +
                      '\n### 课堂伪代码：串起完整系统\n```python\nrun(task_id)\n```\n', encoding='utf-8')
    body = ['## q-001', '', '典型结构：', '后续解释']
    examples = append_examples(body, source, [{'line': 6, 'anchor': 'q-001'}])
    assert len(examples) == 3
    assert examples[-1]['question_anchor'] is None
    assert examples[-1]['association'] == 'chapter_appendix'
    assert examples[1]['question_anchor'] == 'q-001'
    assert '章节附录「课堂伪代码' in '\n'.join(body)
    assert examples[-1]['adaptation'] == 'unchanged'
    assert examples[-1]['source_content_sha256'] == examples[-1]['adapted_sha256']


def test_bilingual_annotations_keep_code_links_and_existing_glosses():
    from scripts.medagent_terminology import annotate_markdown
    from scripts.adapt_medagent_interviews import explanation
    current = explanation(['MedAgent 跨专科按顺序执行。'])
    assert '独立只读步骤可并行' in current and '批准写动作的计划串行' in current
    assert '跨专科按顺序执行' not in current
    text = ('<!-- BEGIN MEDAGENT FULL QUESTION LIBRARY -->\n'
            '## q-001\nFunction Calling 和 `task_id`，FastAPI(Python接口服务框架)。\n'
            '[工具](../backend/mcp_tools.py#task_id)\n'
            '```json\n{"task_id": "123", "drug_name": "阿司匹林"}\n```\n')
    result = annotate_markdown(text)
    assert 'Function Calling(函数调用)' in result
    assert '`task_id`(任务标识)' in result
    assert 'FastAPI(Python接口服务框架)' in result
    assert '[工具](../backend/mcp_tools.py#task_id)' in result
    assert '## q-001\n' in result
    assert '<!-- BEGIN MEDAGENT FULL QUESTION LIBRARY -->' in result
    assert '{"task_id": "123", "drug_name": "阿司匹林"}' in result
    assert '`drug_name`(药品名称)' in result
    assert annotate_markdown(result) == result


def test_react_reasoning_gloss_is_distinct_from_react_ui_and_keeps_code():
    from scripts.medagent_terminology import annotate_markdown
    source = ('ReAct 与 React；`ReAct`。ReAct(前端组件框架)。\n'
              '```text\nReAct(前端组件框架) React\n```\n')
    result = annotate_markdown(source)
    assert 'ReAct(推理与行动交替) 与 React(前端组件框架)' in result
    assert '`ReAct`(推理与行动交替)' in result
    assert '。ReAct(推理与行动交替)。' in result
    assert '```text\nReAct(前端组件框架) React\n```' in result
    assert annotate_markdown(result) == result


def test_original_capture_rejects_replacement_and_does_not_touch_existing_file(tmp_path, monkeypatch):
    import pytest
    from scripts import adapt_medagent_interviews as adapter
    originals = tmp_path / 'originals'
    originals.mkdir()
    monkeypatch.setattr(adapter, 'PRESERVED', originals)
    captured = originals / '00-overview.txt'
    captured.write_bytes(b'original bytes\r\n')
    stamp = captured.stat().st_mtime_ns
    adapter.preserve_original(captured)
    source = tmp_path / '00-overview.md'
    source.write_bytes(captured.read_bytes())
    adapter.preserve_original(source)
    assert captured.stat().st_mtime_ns == stamp
    source.write_text('changed original', encoding='utf-8')
    with pytest.raises(ValueError, match='拒绝覆写'):
        adapter.preserve_original(source)
    assert captured.read_bytes() == b'original bytes\r\n'
    assert captured.stat().st_mtime_ns == stamp


def test_refresh_handles_translated_paragraphs_and_rejects_missing_or_duplicate_targets():
    import pytest
    from scripts.annotate_medagent_documents import replace_paragraph, review_block
    text = '开头不改\n136 个示例均只有一份正文：128 个，Agent(智能体)。\n结尾不改\n'
    result = replace_paragraph(text, '136 个示例均只有一份正文：', '136 个示例均只有一份正文：111/8/17。')
    assert result == '开头不改\n136 个示例均只有一份正文：111/8/17。\n结尾不改\n'
    for invalid in ('没有指定段落', text + text):
        with pytest.raises(ValueError, match='不能静默跳过'):
            replace_paragraph(invalid, '136 个示例均只有一份正文：', 'new')
    marked = review_block(result, '本轮记录')
    assert review_block(marked, '本轮记录') == marked
    assert '本轮记录' not in review_block(marked, '最新记录')


def test_regression_evidence_never_invents_a_historical_count_without_validation():
    from scripts.annotate_medagent_documents import regression_evidence
    missing = regression_evidence(None)
    assert '479' not in missing and '项通过' not in missing
    preserved = regression_evidence(None,{'passed':9})
    assert '仓库记录的代码与依赖回归 9 项通过' in preserved
    assert '不表示本机刚执行' in preserved
    local = regression_evidence({'test_counts':{'passed':10},'elapsed_seconds':1.2,'run_id':'new-run'})
    assert '本轮代码与依赖回归 10 项通过' in local and 'new-run' in local


@pytest.mark.parametrize('extra_chapter', [None, '02-新增.md', '02-新增.MD', '专题/02-新增.MD'])
def test_refresh_corrects_legacy_delivery_scope_and_remains_idempotent(tmp_path, monkeypatch, extra_chapter, case_sensitive_markdown_globs):
    import json
    from types import SimpleNamespace
    from scripts import annotate_medagent_documents as annotator
    library = tmp_path / 'docs/MedAgent_逐题适配'
    library.mkdir(parents=True)
    engineering = tmp_path / 'docs/MedAgent_文档对照与工程改进_2026-10-13.md'
    contents = {
        tmp_path / 'README.md': '# 项目\n2026-10-13 完整话术补充：旧\n2026-10-13 文档追溯复核：旧\n2026-10-13 复核修复：旧\n',
        engineering: '# 工程\n核查日期：旧\n先前逐题生成器跳过了围栏块，旧\n136 个示例均只有一份正文：旧\n链接审计统一覆盖旧\n文档生成与审计修复范围旧\n桌面同步包含项目文档及简历 HTML/PDF，共 23 个文件。\n',
        tmp_path / 'docs/MedAgent_项目理解指南.md': '# 指南\n话术中的原文行号指向旧\n',
        tmp_path / 'docs/MedAgent_AI_多Agent项目话术.md': '# 话术\nReAct 是受控工具循环，Agent 不需要反复插入译注。\n',
        library / 'README.md': '# 题库\n',
        library / '01-示例.md': '# 调度\n',
    }
    if extra_chapter:
        contents[library / extra_chapter] = '# 新增章节\nAgent 负责调用工具。\n'
    for path, body in contents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
    coverage = {'question_count': 1, 'example_count': 1, 'examples': [{'association': 'question', 'placement': 'beside_question'}],
                'example_adaptation_counts': {'unchanged': 1, 'tob_rewrite': 0}}
    (library / 'coverage.json').write_text(json.dumps(coverage), encoding='utf-8')
    monkeypatch.setattr(annotator, 'ROOT', tmp_path)
    monkeypatch.setattr(annotator, 'latest_test_validation', lambda: None)
    monkeypatch.setattr(annotator, 'repository_markdown_paths', lambda _root: set(contents))
    monkeypatch.setattr(annotator.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b''))
    annotator.refresh()
    assert (tmp_path / 'docs/MedAgent_AI_多Agent项目话术.md').read_text(encoding='utf-8') == contents[tmp_path / 'docs/MedAgent_AI_多Agent项目话术.md']
    paragraph = next(line for line in engineering.read_text(encoding='utf-8').splitlines() if line.startswith('桌面同步'))
    # Three library files, three main documents and two diagrams; inventory grows.
    assert f'共 {8 + int(bool(extra_chapter))} 个目标' in paragraph
    assert '单独交付，不计入此清单' in paragraph and '共 23 个文件' not in paragraph
    for path in (tmp_path/'README.md', engineering, tmp_path/'docs/MedAgent_项目理解指南.md'):
        assert f'和 {11 + 2*int(bool(extra_chapter))} 份本地链接' in path.read_text(encoding='utf-8')
    if extra_chapter:
        assert 'Agent(智能体)' in (library/extra_chapter).read_text(encoding='utf-8')
    from scripts import verify_delivery_evidence as verifier
    monkeypatch.setattr(verifier, 'ROOT', tmp_path)
    monkeypatch.setattr(verifier, 'repository_markdown_paths', lambda _root: set(contents))
    verifier.verify_document_scope_claims(coverage, 5 + int(bool(extra_chapter)))
    readme = (tmp_path/'README.md').read_text(encoding='utf-8')
    assert '话术合并继承分章已有译注' in readme and '主文由生成器重建' in readme
    first = {path: path.read_bytes() for path in contents}
    annotator.refresh()
    assert {path: path.read_bytes() for path in contents} == first


def test_filename_annotations_are_removed_but_professional_terms_remain():
    from scripts.medagent_terminology import annotate_markdown
    source = '原件 .txt(纯文本文件扩展名)，适配稿 `.md`(Markdown文档格式)；Redis 和 `patient_id`。'
    result = annotate_markdown(source)
    assert '.txt(' not in result and '`.md`(' not in result
    assert 'Redis(内存键值数据库)' in result and '`patient_id`(患者标识)' in result
    assert annotate_markdown(result) == result


@pytest.mark.parametrize('uri', [
    'mysql+aiomysql://',
    'MYSQL+AIOMYSQL://user:fixture@localhost/db?charset=utf8mb4',
    'postgresql+asyncpg://user:fixture@localhost/db',
    'sqlite+aiosqlite:///./medagent.db',
    'redis://localhost:6379/0',
    'mongodb://localhost/fixture',
    'https://example.invalid/mysql/task_id',
    'custom+driver://localhost/Redis',
])
@pytest.mark.parametrize('inline', [False, True])
def test_uri_literals_are_preserved_without_database_glosses(uri, inline):
    from scripts.medagent_terminology import annotate_markdown
    literal = f'`{uri}`' if inline else uri
    source = f'连接地址：{literal}；仍解释 MySQL 和 `task_id`。'
    expected = f'连接地址：{literal}；仍解释 MySQL(关系型数据库) 和 `task_id`(任务标识)。'
    assert annotate_markdown(source) == expected
    assert annotate_markdown(expected) == expected


@pytest.mark.parametrize('source,expected', [
    ('`mysql+aiomysql://`(关系型数据库)', '`mysql+aiomysql://`'),
    ('mysql+aiomysql://（关系型数据库）', 'mysql+aiomysql://'),
    ('`redis://localhost/0`(内存键值数据库)', '`redis://localhost/0`'),
    ('`sqlite+aiosqlite:///./data.db`(轻量关系型数据库)', '`sqlite+aiosqlite:///./data.db`'),
    ('mysql(关系型数据库)+aiomysql://', 'mysql+aiomysql://'),
    ('`mysql(关系型数据库)+aiomysql://`', '`mysql+aiomysql://`'),
    ('`mysql+aiomysql://`（生产连接前缀）', '`mysql+aiomysql://`（生产连接前缀）'),
])
def test_uri_gloss_repair_preserves_literals_and_human_remarks(source, expected):
    from scripts.medagent_terminology import annotate_markdown
    assert annotate_markdown(source) == expected
    assert annotate_markdown(expected) == expected


def test_uri_annotation_keeps_markdown_targets_and_fenced_examples_exact():
    from scripts.medagent_terminology import annotate_markdown
    target = '[数据库](mysql+aiomysql://localhost/db)'
    code = '```text\nmysql(关系型数据库)+aiomysql://\n```'
    result = annotate_markdown(target+'\n\n'+code)
    assert target in result and code in result
    assert annotate_markdown(result) == result


def test_template_text_line_is_not_a_table_column_and_repeated_cells_have_distinct_spans():
    from scripts.adapt_medagent_interviews import question_source_position
    question = '主 Agent 做什么？'
    template = '→ ' + question
    position = question_source_position(template, question, None)
    assert position['column'] is None and position['source_position_kind'] == 'text_line'
    assert template[position['source_start_column']-1:position['source_end_column']] == question
    row = f'| {question} | {question} | 追问 |'
    first = question_source_position(row, question, 1)
    second = question_source_position(row, question, 2)
    assert first['source_start_column'] != second['source_start_column']
    assert row[second['source_start_column']-1:second['source_end_column']] == question


def test_merge_builds_reference_without_overwriting_readable_main(tmp_path, monkeypatch):
    import json
    from scripts import merge_medagent_interviews as merger
    library = tmp_path / 'library'
    library.mkdir()
    main = library / '核心题理解导读.md'
    main.write_text('# 面试主文\n\n手工整理的回答和例子。\n', encoding='utf-8')
    main_bytes = main.read_bytes()
    book = tmp_path / 'MedAgent_AI_多Agent项目话术.md'
    chapter = '00-示例.md'
    (library / chapter).write_text('## q-001\n\n### 主问题｜怎么做？\n保持答案。\n'
                                  '### example-001\n```json\n{"patient_id": "a"}\n```\n', encoding='utf-8')
    coverage = {'source_files': [{'name': chapter}], 'question_count': 1, 'example_count': 1,
                'questions': [{'output': chapter, 'anchor': 'q-001'}],
                'examples': [{'output': chapter, 'anchor': 'example-001', 'association': 'chapter_appendix'}]}
    (library / 'coverage.json').write_text(json.dumps(coverage), encoding='utf-8')
    monkeypatch.setattr(merger, 'ROOT', tmp_path)
    monkeypatch.setattr(merger, 'LIBRARY', library)
    monkeypatch.setattr(merger, 'BOOK', book)
    merger.merge()
    text = book.read_text(encoding='utf-8')
    assert '0 个示例随对应问答保留，0 个章首示例单列概览，1 个独立段落示例单列章节附录' in text
    assert '完整保留 1 处问答' in text and '全部原题及答案仍在本文件' in text
    assert main.read_bytes() == main_bytes
    assert '{"patient_id": "a"}' in text and '保持答案。' in text
    assert json.loads((library/'coverage.json').read_text(encoding='utf-8'))['full_question_document'] == 'MedAgent_AI_多Agent项目话术.md'
    merger.merge()
    assert book.read_text(encoding='utf-8') == text and main.read_bytes() == main_bytes


@pytest.mark.parametrize('extension_after_example', [False, True])
def test_readable_merge_expands_cross_answers_and_preserves_code(tmp_path, monkeypatch, extension_after_example):
    import hashlib
    import json
    from scripts import merge_medagent_interviews as merger
    library = tmp_path / 'library'
    library.mkdir()
    code = 'def keep():\n    text = """\n**在 MedAgent 中怎么补全：** 这是代码内的文字。\n"""\n\n\n    return "unchanged"'
    extension = '\n**在 MedAgent 中怎么补全：** 后续能力需要单独设计。\n'
    primary, cross = '04-示例.md', '横向.md'
    (library/primary).write_text('## q-001\n\n### 主问题｜能重规划吗？\n\n'
        '原文位置：原件第 1 行。\n\n**项目口述：** 项目仅允许一次只读复核。\n\n'
        '**针对本题的设计展开（原理）：**\n\n只修改尚未执行且不再适用的步骤。\n\n'
        + ('' if extension_after_example else extension)
        + '**代码依据：** 无\n\n### example-001\n```python\n'+code+'\n```\n'
        + (extension if extension_after_example else ''), encoding='utf-8')
    (library/cross).write_text('## q-001\n\n### 能重规划吗？\n\n来源：表格。\n\n'
        '**项目口述：** 项目仅允许一次只读复核。\n\n'
        '**对应详解：** [完整回答](04-示例.md#q-001)。\n', encoding='utf-8')
    guide = library/'核心题理解导读.md'
    guide.write_text('### Q20 重规划\n\n**例子：** A 已完成，后续 C 需要调整。\n', encoding='utf-8')
    before = guide.read_bytes()
    coverage = {'source_files': [{'name': primary},{'name': cross}], 'question_count': 2, 'example_count': 1,
        'questions': [{'output': name,'anchor':'q-001','question':'能重规划吗？'} for name in (primary,cross)],
        'examples': [{'output':primary,'anchor':'example-001','association':'question'}]}
    (library/'coverage.json').write_text(json.dumps(coverage), encoding='utf-8')
    monkeypatch.setattr(merger, 'ROOT', tmp_path)
    monkeypatch.setattr(merger, 'LIBRARY', library)
    monkeypatch.setattr(merger, 'BOOK', tmp_path/'book.md')
    merger.merge()
    text = (tmp_path/'book.md').read_text(encoding='utf-8')
    assert text.count('只修改尚未执行且不再适用的步骤。') == 2
    assert '**回答思路：**' in text and '**用例理解：**' in text
    assert '[分章原题](library/04-示例.md#q-001)' in text
    actual = text.split('```python\n',1)[1].split('\n```',1)[0]
    assert hashlib.sha256(actual.encode()).digest() == hashlib.sha256(code.encode()).digest()
    assert guide.read_bytes() == before
    section = merger.question_sections(text, json.loads((library/'coverage.json').read_text(encoding='utf-8')))['full-04-q-001']
    assert '**在 MedAgent' not in section
    assert section.index('**先讲原理：**') < section.index('**项目里怎么说：**') < section.index('**扩展与限制：**')


def test_reading_manifest_records_each_supplement_and_rejects_counter_drift(tmp_path, monkeypatch):
    import json
    from scripts import merge_medagent_interviews as merger
    from scripts.verify_delivery_evidence import verify_reading_layout
    library = tmp_path/'library'
    library.mkdir()
    source, cross = '04-示例.md', '横向.md'
    question = '子 Agent 负责业务还是工具？'
    (library/source).write_text('## q-005\n\n### 主问题｜'+question+'\n\n**项目口述：** 三个专科分工。\n\n'
        '**方案补全：** 扩展需复核。\n', encoding='utf-8')
    (library/cross).write_text('## q-001\n\n### '+question+'\n\n**项目口述：** 三个专科分工。\n\n'
        '**方案补全：** 扩展需复核。\n\n**对应详解：** [详解](04-示例.md#q-005)。\n', encoding='utf-8')
    coverage = {'source_files':[{'name':source},{'name':cross}], 'question_count':2, 'example_count':0, 'examples':[],
        'questions':[{'output':source,'anchor':'q-005','question':question},
                     {'output':cross,'anchor':'q-001','question':question}]}
    (library/'coverage.json').write_text(json.dumps(coverage),encoding='utf-8')
    monkeypatch.setattr(merger,'ROOT',tmp_path)
    monkeypatch.setattr(merger,'LIBRARY',library)
    monkeypatch.setattr(merger,'BOOK',tmp_path/'book.md')
    merger.merge()
    manifest = json.loads((library/'coverage.json').read_text(encoding='utf-8'))
    layout = manifest['reading_layout']
    assert layout['supplemented_question_count'] == 2
    assert {row['book_anchor'] for row in layout['supplemented_questions']} == {'full-04-q-005','full-cross-q-001'}
    assert {row['source_key'] for row in layout['supplemented_questions']} == {'04-q-005'}
    text = (tmp_path/'book.md').read_text(encoding='utf-8')
    verify_reading_layout(text,manifest)
    layout['supplemented_question_count'] = 1
    with pytest.raises(ValueError,match='layout manifest differs'):
        verify_reading_layout(text,manifest)
