from scripts.adapt_medagent_interviews import source_examples, append_examples


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


def test_filename_annotations_are_removed_but_professional_terms_remain():
    from scripts.medagent_terminology import annotate_markdown
    source = '原件 .txt(纯文本文件扩展名)，适配稿 `.md`(Markdown文档格式)；Redis 和 `patient_id`。'
    result = annotate_markdown(source)
    assert '.txt(' not in result and '`.md`(' not in result
    assert 'Redis(内存键值数据库)' in result and '`patient_id`(患者标识)' in result
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


def test_merge_rebuilds_stale_summary_without_changing_question_or_example(tmp_path, monkeypatch):
    import json
    from scripts import merge_medagent_interviews as merger
    library = tmp_path / 'library'
    library.mkdir()
    book = tmp_path / 'book.md'
    book.write_text('# 项目\n\n全文保留 1,260 处问答与 136 个图、伪代码和结构示例。128/8 旧口径。\n'
                    '生产签名委托必带任务、会话。当前完整工程回归 479 项通过，医疗正确性仍須复核。\n', encoding='utf-8')
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
    monkeypatch.setattr(merger, 'latest_test_validation', lambda: {'test_counts': {'passed': 480}})
    merger.merge()
    text = book.read_text(encoding='utf-8')
    assert '128/8 旧口径' not in text
    assert '0 个示例随对应问答保留，0 个章首示例单列概览，1 个独立段落示例单列章节附录' in text
    assert '最新代码与依赖回归 480 项通过' in text and '当前完整工程回归 479' not in text
    assert '桌面同名 .md 为适配稿' in text and '生成文档另行核验' in text
    assert '{"patient_id": "a"}' in text and '保持答案。' in text
    merger.merge()
    assert book.read_text(encoding='utf-8') == text
