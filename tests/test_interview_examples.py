from scripts.adapt_medagent_interviews import source_examples, append_examples


def test_all_fenced_examples_survive_including_before_first_question(tmp_path):
    source = tmp_path / "00-overview.txt"
    source.write_text('# 概览\n```mermaid\nflowchart TD\n A[查订单] --> B[退款评估]\n```\n'
                      '## Q1｜怎么调度？\n```json\n{"order_id": "12345"}\n```\n', encoding="utf-8")
    rows = source_examples(source)
    assert len(rows) == 2 and [r['language'] for r in rows] == ['mermaid', 'json']
    assert rows[0]['line'] == 2 and rows[1]['line'] == 7
    assert '订单' not in rows[0]['content'] and '退款' not in rows[0]['content']
    assert 'patient_id' in rows[1]['content']
    body = []
    examples = append_examples(body, source, [{'line': 6, 'anchor': 'q-001'}])
    assert examples[0]['question_anchor'] is None
    assert examples[1]['question_anchor'] == 'q-001'
    assert '不能作为当前项目已实现' in '\n'.join(body)
    assert '\n'.join(body).count('```') == 4
