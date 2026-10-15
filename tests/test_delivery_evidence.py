import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from scripts import verify_delivery_evidence as verifier
from scripts import preflight_git


@pytest.mark.parametrize('extra', [None, 'nested/02-extra.md'])
def test_default_desktop_plan_tracks_inventory_and_excludes_separate_materials(tmp_path, extra):
    root, desktop = tmp_path / 'repo', tmp_path / 'desktop'
    library = root / 'docs/MedAgent_逐题适配'
    library.mkdir(parents=True)
    (library / 'README.md').write_text('# Library', encoding='utf-8')
    (library / 'coverage.json').write_text('{}', encoding='utf-8')
    if extra:
        path = library / extra
        path.parent.mkdir()
        path.write_text('# Extra', encoding='utf-8')
    (root / 'docs/med_rag_面试全解.md').write_text('# Separate', encoding='utf-8')
    (root / 'docs/resume.html').write_text('Separate resume', encoding='utf-8')
    pairs = verifier.default_desktop_pairs(root, desktop)
    assert len(pairs) == 7 + int(extra is not None)
    assert len({target for _, target in pairs}) == len(pairs)
    assert (library / 'coverage.json', desktop / 'MedAgent_AI_多Agent项目/coverage.json') in pairs
    if extra:
        assert (library / extra, desktop / 'MedAgent_AI_多Agent项目' / extra) in pairs
    assert {source.name for source, _ in pairs} >= {
        'MedAgent_AI_多Agent项目话术.md', 'MedAgent_项目理解指南.md',
        'MedAgent_文档对照与工程改进_2026-10-13.md', 'MedAgent_项目流程图.svg', 'MedAgent_项目流程图.png'}
    assert all(source.suffix not in {'.html', '.pdf'} and 'med_rag' not in source.name for source, _ in pairs)
    assert not desktop.exists()


def logs(tests=37, static_count=3):
    static = ''.join(f'[PASS] static check {index}\n' for index in range(static_count))
    conclusion = '结论：检查通过，可以进入提交/推送流程。\n'
    full = static + f'[PASS] pytest 通过：{tests} passed, 2 warnings in 1.23s\n'
    full += '[PASS] pip check 通过：No broken requirements found.\n' + conclusion
    return full, static + conclusion


@pytest.mark.parametrize('tests,static_count', [(37, 3), (92, 7)])
def test_counts_are_read_from_logs(tests, static_count):
    assert verifier.parse_preflight_logs(*logs(tests, static_count)) == {
        'pytest_passed': tests,
        'full_preflight_passed': static_count + 2,
        'quick_preflight_passed': static_count,
    }


@pytest.mark.parametrize('change', [
    'warn', 'fail', 'missing_pytest', 'missing_pip', 'mismatched_static',
    'duplicate_pass', 'failed_pytest_summary', 'missing_conclusion',
])
def test_log_verification_rejects_invalid_evidence(change):
    full, quick = logs()
    if change == 'warn':
        quick += '[WARN] needs review\n'
    elif change == 'fail':
        full += '[FAIL] rejected\n'
    elif change == 'missing_pytest':
        full = full.replace('[PASS] pytest', '[NOTE] pytest')
    elif change == 'missing_pip':
        full = full.replace('[PASS] pip check', '[NOTE] pip check')
    elif change == 'mismatched_static':
        quick = quick.replace('static check 0', 'other check')
    elif change == 'duplicate_pass':
        full += '[PASS] static check 0\n'
        quick += '[PASS] static check 0\n'
    elif change == 'failed_pytest_summary':
        full = full.replace('37 passed, 2 warnings', '37 passed, 1 failed, 2 warnings')
    else:
        full = full.replace('结论：检查通过，可以进入提交/推送流程。', '')
    with pytest.raises(ValueError):
        verifier.parse_preflight_logs(full, quick)


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'docs').mkdir(parents=True)
    directory = tmp_path / 'evidence'
    directory.mkdir()
    desktop = tmp_path / 'desktop'
    desktop.mkdir()
    monkeypatch.setattr(verifier, 'ROOT', root)
    monkeypatch.setattr(verifier, 'verification_head', lambda: 'fixture-head')
    full, quick = logs()
    (directory / 'preflight.log').write_text(full, encoding='utf-8')
    (directory / 'quick.log').write_text(quick, encoding='utf-8')
    manifest, entries = [], []
    for name in verifier.PRIMARY_DOCUMENTS:
        repo_source = root / 'docs' / name
        repo_source.write_text('# 文档\n\n正文。[验证](../README.md#验证)\n', encoding='utf-8', newline='\n')
        projected = directory / name
        target = desktop / name
        data = verifier.project_desktop_document(repo_source)
        projected.write_bytes(data)
        target.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest.append({'source': str(projected), 'target': str(target), 'after_sha256': digest})
        entries.append({'target': str(target), 'sha256': digest})
    write_json(directory, 'desktop_manifest.json', manifest)
    write_json(directory, 'latest_desktop_receipt.json', {'count': len(entries), 'entries': entries})
    write_json(directory, 'document_links.json', {'files': 7, 'issues': []})
    write_json(directory, 'anchor_drift.json', [
        {'surface': surface, 'change': change, 'valid_install_fixture': True,
         'result': 'passed' if change == 'original' else 'blocked_before_gate'}
        for surface in ('docker', 'ci') for change in ('original', 'missing', 'duplicate')
    ])
    return directory, root, manifest, entries


def write_json(directory, name, data):
    (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def test_shared_mapping_preserves_code_and_uses_desktop_targets(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    source = root / 'docs/book.md'
    source.parent.mkdir(parents=True)
    chapter = root / 'docs/library/chapter.md'
    chapter.parent.mkdir()
    chapter.write_text('# Chapter', encoding='utf-8')
    backend = root / 'backend/tool.py'
    backend.parent.mkdir()
    backend.write_text('# Tool', encoding='utf-8')
    target = tmp_path / 'desktop/book.md'
    mapped = target.parent / '题库/chapter name.md'
    content = ('[题库](library/chapter.md#q-001)\n[代码](../backend/tool.py)\n'
               '[链接](https://example.invalid/)\n`[原样](library/chapter.md)`\n'
               '```text\n[原样](library/chapter.md)\n```\n')
    result = verifier.rebase_markdown(source, target, content, {chapter.resolve(): mapped}, root)
    assert '[题库](<题库/chapter name.md#q-001>)' in result
    assert f'[代码]({backend.as_posix()})' in result
    assert '[链接](https://example.invalid/)' in result
    assert '`[原样](library/chapter.md)`' in result
    assert '```text\n[原样](library/chapter.md)\n```' in result
    monkeypatch.setattr(verifier, 'ROOT', root)
    source.write_bytes(content.replace('\n', '\r\n').encode('utf-8'))
    projected = verifier.project_desktop_document(source, target, {chapter.resolve(): mapped})
    assert projected == result.replace('\n', '\r\n').encode('utf-8')


@pytest.fixture
def current_delivery(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    library = root / 'docs/MedAgent_逐题适配'
    library.mkdir(parents=True)
    directory = root / 'artifacts/current'
    directory.mkdir(parents=True)
    source = directory / 'original.txt'
    original = '# Q1｜怎么调度？\n```json\n{}\n```\n'
    source.write_text(original, encoding='utf-8')
    original_path = source.relative_to(root).as_posix()
    block = next(re.finditer(r'^```[^\n]*\n(.*?)^```\s*$', original, re.M | re.S))
    coverage = {'question_count': 1, 'example_count': 1,
                'source_files': [{'original_local_path': original_path, 'sha256': verifier.sha(source)}],
                'questions': [{'original_local_path': original_path, 'line': 1, 'question': '怎么调度？', 'book_anchor': 'full-q'}],
                'examples': [{'original_local_path': original_path, 'line': 2, 'book_anchor': 'full-example',
                              'language': 'json', 'source_sha256': hashlib.sha256(block[0].encode()).hexdigest(),
                              'source_content_sha256': hashlib.sha256(b'{}').hexdigest(),
                              'adapted_sha256': hashlib.sha256(b'{}').hexdigest(), 'adaptation': 'unchanged'}],
                'example_adaptation_counts': {'unchanged': 1, 'tob_rewrite': 0}}
    write_json(library, 'coverage.json', coverage)
    book = root / 'docs/MedAgent_AI_多Agent项目话术.md'
    book.write_text('# Book\n本轮代码与依赖回归 5 项通过。\n### full-q\n怎么调度？\n'
                    '#### full-example\n```json\n{}\n```\n', encoding='utf-8', newline='\n')
    (root / 'README.md').write_text('# Fixture\n', encoding='utf-8')
    target = tmp_path / 'desktop/MedAgent_AI_多Agent项目话术.md'
    target.parent.mkdir()
    target.write_bytes(book.read_bytes())
    receipt = {'entries': [{'source': str(book), 'target': str(target),
                           'source_sha256': verifier.sha(book), 'target_sha256': verifier.sha(target)}]}
    write_json(directory, 'desktop_sync_receipt.json', receipt)
    report = {'validation_scope': 'current_documents_and_original_references',
              'current_document_sha256': {str(book): verifier.sha(book), str(target): verifier.sha(target)},
              'document_links_checked': 2, 'coverage_sha256': verifier.sha(library/'coverage.json'),
              'book_sha256': verifier.sha(book), 'question_count': 1, 'example_count': 1,
              'example_adaptations_verified': coverage['example_adaptation_counts'], 'desktop_hashes_verified': 1}
    write_json(directory, 'report.json', report)
    write_json(directory, 'pointer.json', {'report': str(directory/'report.json'), 'report_sha256': verifier.sha(directory/'report.json')})
    monkeypatch.setattr(verifier, 'ROOT', root)
    monkeypatch.setattr(verifier, 'verification_head', lambda: 'fixture-head')
    monkeypatch.setattr(verifier, 'latest_test_validation', lambda: {'test_counts': {'passed': 5}, 'run_id': 'fixture-run'})
    return directory, book, target, report, receipt


def test_current_delivery_verifies_claims_and_can_repeat_without_writing(current_delivery):
    directory, book, target, _, _ = current_delivery
    before = (book.read_bytes(), target.read_bytes())
    result = verifier.verify_current_delivery(directory/'pointer.json')
    assert result['pytest_passed'] == 5 and result['questions_verified'] == result['examples_verified'] == 1
    assert result['document_files_checked'] == 2 and result['desktop_targets_matched'] == 1
    assert result['verifier_path'] == 'scripts/verify_delivery_evidence.py'
    assert verifier.verify_current_delivery(directory/'pointer.json')['test_run_id'] == 'fixture-run'
    assert before == (book.read_bytes(), target.read_bytes())


@pytest.mark.parametrize('change', [None, 'count', 'target'])
def test_current_delivery_checks_default_plan_and_document_count(current_delivery, change):
    directory, book, target, report, _ = current_delivery
    root, desktop = book.parents[1], target.parent
    guide = root / 'docs/MedAgent_项目理解指南.md'
    guide.write_text('# Guide\n', encoding='utf-8')
    engineering = root / 'docs' / verifier.DESKTOP_DOCUMENTS[-1]
    engineering.write_text('# Engineering\n', encoding='utf-8')
    diagrams = root / 'artifacts/medagent_upgrade_20261013'
    diagrams.mkdir(parents=True)
    (diagrams / 'MedAgent_项目流程图.svg').write_text('<svg/>', encoding='utf-8')
    (diagrams / 'MedAgent_项目流程图.png').write_bytes(b'fixture-image')
    pairs = verifier.default_desktop_pairs(root, desktop)
    claimed = len(pairs) + int(change == 'count')
    engineering.write_text(f'# Engineering\n桌面同步的当前默认清单，共 {claimed} 个目标。\n', encoding='utf-8')
    if change == 'target':
        pairs = [(source, destination.with_name('unexpected.md') if source == guide else destination)
                 for source, destination in pairs]
    mapping = {source.resolve(): destination for source, destination in pairs}
    entries, documents = [], {}
    for source, destination in pairs:
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = (verifier.rebase_markdown(source, destination, source.read_bytes().decode('utf-8-sig'), mapping, root).encode('utf-8')
                if source.suffix == '.md' else source.read_bytes())
        destination.write_bytes(data)
        entries.append({'source': str(source), 'target': str(destination),
                        'source_sha256': verifier.sha(source), 'target_sha256': verifier.sha(destination)})
        if source.suffix == '.md':
            documents.update({str(source): verifier.sha(source), str(destination): verifier.sha(destination)})
    report.update(current_document_sha256=documents, document_links_checked=len(documents), desktop_hashes_verified=len(entries))
    write_json(directory, 'desktop_sync_receipt.json', {'entries': entries})
    write_json(directory, 'report.json', report)
    write_json(directory, 'pointer.json', {'report': str(directory/'report.json'), 'report_sha256': verifier.sha(directory/'report.json')})
    before = {path: path.read_bytes() for path in [*map(Path, documents), directory/'report.json', directory/'desktop_sync_receipt.json']}
    if change:
        reason = 'default desktop target claim differs' if change == 'count' else 'default desktop plan differs'
        with pytest.raises(ValueError, match=reason):
            verifier.verify_current_delivery(directory/'pointer.json')
    else:
        assert verifier.verify_current_delivery(directory/'pointer.json')['desktop_targets_matched'] == len(pairs)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize('change', ['report', 'document', 'missing_regression', 'forged_claim'])
def test_current_delivery_rejects_stale_or_forged_claims(current_delivery, monkeypatch, change):
    directory, book, target, report, receipt = current_delivery
    if change == 'report':
        (directory/'report.json').write_text('{}', encoding='utf-8')
    elif change == 'document':
        book.write_text('changed', encoding='utf-8')
    elif change == 'missing_regression':
        monkeypatch.setattr(verifier, 'latest_test_validation', lambda: None)
    else:
        book.write_text(book.read_text(encoding='utf-8').replace('5 项通过', '999 项通过'), encoding='utf-8', newline='\n')
        target.write_bytes(book.read_bytes())
        report['book_sha256'] = verifier.sha(book)
        report['current_document_sha256'] = {str(book): verifier.sha(book), str(target): verifier.sha(target)}
        receipt['entries'][0].update(source_sha256=verifier.sha(book), target_sha256=verifier.sha(target))
        write_json(directory, 'desktop_sync_receipt.json', receipt)
        write_json(directory, 'report.json', report)
        write_json(directory, 'pointer.json', {'report': str(directory/'report.json'), 'report_sha256': verifier.sha(directory/'report.json')})
    reason = {'report': 'report hash differs', 'document': 'current document hash differs',
              'missing_regression': 'no current successful code regression',
              'forged_claim': 'current document test claim differs'}[change]
    with pytest.raises(ValueError, match=reason):
        verifier.verify_current_delivery(directory/'pointer.json')


def test_summary_checks_source_target_and_repository_content(evidence):
    directory, _, _, _ = evidence
    result = verifier.summarize(directory)
    assert result['pytest_passed'] == 37
    assert result['document_files_checked'] == 7
    assert result['desktop_targets_matched'] == 2
    assert result['desktop_manifest_targets_matched'] == 2
    assert result['repository_desktop_documents_matched'] == 2
    assert 'desktop_manifest.json' in result['files']


@pytest.mark.parametrize('change', [
    'missing_manifest', 'missing_projected', 'changed_projected',
    'changed_repository', 'rewritten_desktop_and_hashes',
    'missing_primary_mapping', 'duplicate_manifest_target',
])
def test_desktop_verification_rejects_incomplete_or_stale_sync(evidence, change):
    directory, root, manifest, entries = evidence
    if change == 'missing_manifest':
        (directory / 'desktop_manifest.json').unlink()
    elif change == 'missing_projected':
        Path(manifest[0]['source']).unlink()
    elif change == 'changed_projected':
        Path(manifest[0]['source']).write_text('tampered', encoding='utf-8')
    elif change == 'changed_repository':
        (root / 'docs' / verifier.PRIMARY_DOCUMENTS[0]).write_text('new repository text', encoding='utf-8')
    elif change == 'rewritten_desktop_and_hashes':
        # A self-consistent forged source/target/receipt still differs from the repository.
        data = b'tampered desktop content'
        Path(manifest[0]['source']).write_bytes(data)
        Path(manifest[0]['target']).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest[0]['after_sha256'] = digest
        entries[0]['sha256'] = digest
        write_json(directory, 'desktop_manifest.json', manifest)
        write_json(directory, 'latest_desktop_receipt.json', {'count': len(entries), 'entries': entries})
    elif change == 'missing_primary_mapping':
        write_json(directory, 'desktop_manifest.json', manifest[:1])
    else:
        write_json(directory, 'desktop_manifest.json', manifest + manifest[:1])
    with pytest.raises((ValueError, FileNotFoundError)):
        verifier.summarize(directory)


def test_cli_preserves_existing_summary_and_can_repeat_without_output(evidence, monkeypatch, capsys):
    directory, _, _, _ = evidence
    output = directory / 'summary.json'
    monkeypatch.setattr('sys.argv', ['verify_delivery_evidence', '--evidence-dir', str(directory), '--output', str(output)])
    assert verifier.main() == 0
    original = output.read_bytes()
    assert verifier.main() == 1
    assert output.read_bytes() == original
    assert 'Evidence verification failed' in capsys.readouterr().err
    monkeypatch.setattr('sys.argv', ['verify_delivery_evidence', '--evidence-dir', str(directory)])
    assert verifier.main() == 0
    assert json.loads(capsys.readouterr().out)['pytest_passed'] == 37


@pytest.mark.parametrize('change,reason', [
    ('empty_manifest', 'manifest: missing synchronization entries'),
    ('manifest_target_not_in_receipt', 'manifest: target missing from current receipt'),
    ('projected_source_outside_evidence', 'manifest: projected source outside evidence directory'),
    ('manifest_hash_mismatch', 'manifest: source/receipt hash differs'),
    ('source_target_bytes_differ', 'manifest: source/target bytes differ'),
    ('receipt_count_mismatch', 'receipt: inconsistent target count'),
    ('duplicate_receipt_target', 'receipt: duplicate targets'),
    ('receipt_target_hash_mismatch', 'receipt: current target hash differs'),
    ('unexpected_anchor_result', 'anchor: unexpected result'),
    ('invalid_install_fixture', 'anchor: invalid installation fixture'),
    ('missing_anchor_scenario', 'anchor: missing or duplicate scenarios'),
    ('unresolved_links', 'links: unresolved issues'),
    ('invalid_link_count', 'links: invalid file count'),
    ('zero_passed_tests', 'full: no passed tests'),
    ('quick_duplicate_pass', 'quick: duplicate successful checks'),
])
def test_each_remaining_rejection_reports_its_specific_reason(evidence, change, reason):
    directory, _, manifest, entries = evidence
    receipt = {'count': len(entries), 'entries': entries}
    if change == 'empty_manifest':
        manifest = []
    elif change == 'manifest_target_not_in_receipt':
        manifest[0]['target'] = str(directory / 'not-in-receipt.md')
    elif change == 'projected_source_outside_evidence':
        outside = directory.parent / 'outside.md'
        outside.write_bytes(Path(manifest[0]['source']).read_bytes())
        manifest[0]['source'] = str(outside)
    elif change == 'manifest_hash_mismatch':
        manifest[0]['after_sha256'] = '0' * 64
    elif change == 'source_target_bytes_differ':
        # Receipt still matches the target: byte comparison must fail before source hash checking.
        Path(manifest[0]['source']).write_bytes(b'changed projected source')
    elif change == 'receipt_count_mismatch':
        receipt['count'] += 1
    elif change == 'duplicate_receipt_target':
        entries.append(dict(entries[0]))
        receipt['count'] += 1
    elif change == 'receipt_target_hash_mismatch':
        entries[0]['sha256'] = '0' * 64
    elif change in ('unexpected_anchor_result', 'invalid_install_fixture', 'missing_anchor_scenario'):
        path = directory / 'anchor_drift.json'
        probe = json.loads(path.read_text(encoding='utf-8'))
        if change == 'unexpected_anchor_result':
            probe[0]['result'] = 'blocked_before_gate'
        elif change == 'invalid_install_fixture':
            probe[0]['valid_install_fixture'] = False
        else:
            probe.pop()
        write_json(directory, path.name, probe)
    elif change in ('unresolved_links', 'invalid_link_count'):
        links = {'files': 7, 'issues': []}
        if change == 'unresolved_links':
            links['issues'] = ['missing link']
        else:
            links['files'] = 0
        write_json(directory, 'document_links.json', links)
    elif change == 'zero_passed_tests':
        full, quick = logs(tests=0)
        (directory / 'preflight.log').write_text(full, encoding='utf-8')
    else:
        full, quick = logs()
        (directory / 'quick.log').write_text(quick + '[PASS] static check 0\n', encoding='utf-8')
    write_json(directory, 'desktop_manifest.json', manifest)
    write_json(directory, 'latest_desktop_receipt.json', receipt)
    with pytest.raises(ValueError) as error:
        verifier.summarize(directory)
    assert str(error.value) == reason


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('fence', ['```', '````', '~~~', '   ```'])
def test_desktop_mapping_preserves_fence_bytes_and_only_rebases_prose(tmp_path, newline, fence):
    root = tmp_path / 'repo'
    source = root / 'book.md'
    chapter = root / 'chapter.md'
    root.mkdir()
    chapter.write_text('# Chapter', encoding='utf-8')
    target = tmp_path / 'desktop/book.md'
    mapped = target.parent / 'chapters/chapter.md'
    literal = '[literal](chapter.md)'
    protected = newline.join([fence + 'text', literal, fence, ''])
    content = protected + '[prose](chapter.md)' + newline
    result = verifier.rebase_markdown(source, target, content, {chapter.resolve(): mapped}, root)
    assert result == protected + '[prose](chapters/chapter.md)' + newline


def test_desktop_mapping_preserves_multiline_code_spans_with_inner_ticks(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    source = root / 'book.md'
    chapter = root / 'chapter.md'
    chapter.write_text('# Chapter', encoding='utf-8')
    target = tmp_path / 'desktop/book.md'
    span = '``before ` [literal](chapter.md)\r\nafter``'
    content = span + '\r\n[prose](chapter.md)\r\n'
    result = verifier.rebase_markdown(source, target, content,
                                      {chapter.resolve(): target.parent/'moved.md'}, root)
    assert result == span + '\r\n[prose](moved.md)\r\n'


def test_repository_gate_works_without_private_originals_or_desktop(current_delivery):
    directory, book, target, _, _ = current_delivery
    (directory / 'original.txt').unlink()
    target.unlink()
    result = verifier.verify_repository_documents()
    assert result['questions_verified'] == result['examples_verified'] == 1
    assert result['document_files_checked'] == 2
    assert 'private originals' in result['scope']
    assert verifier.verify_repository_documents() == result


@pytest.mark.parametrize('change,reason', [
    ('example', 'adapted example differs'),
    ('anchor', 'question anchor missing or duplicated'),
    ('count', 'question count differs'),
    ('link', 'local links failed'),
])
def test_repository_gate_rejects_actual_document_drift(current_delivery, change, reason):
    directory, book, _, _, _ = current_delivery
    if change == 'example':
        book.write_text(book.read_text(encoding='utf-8').replace('\n{}\n', '\n{"changed":true}\n'), encoding='utf-8')
    elif change == 'anchor':
        book.write_text(book.read_text(encoding='utf-8').replace('### full-q', '### changed-q'), encoding='utf-8')
    elif change == 'link':
        with book.open('a', encoding='utf-8') as stream:
            stream.write('\n[broken](missing.md)\n')
    else:
        coverage_path = book.parent / 'MedAgent_逐题适配/coverage.json'
        coverage = json.loads(coverage_path.read_text(encoding='utf-8'))
        coverage['question_count'] += 1
        write_json(coverage_path.parent, coverage_path.name, coverage)
    with pytest.raises(ValueError, match=reason):
        verifier.verify_repository_documents()


def test_current_document_delivery_rejects_shared_resume_scope(current_delivery):
    directory, book, target, report, receipt = current_delivery
    new_target = target.with_suffix('.html')
    target.rename(new_target)
    receipt['entries'][0]['target'] = str(new_target)
    report['current_document_sha256'] = {str(book): verifier.sha(book)}
    report['document_links_checked'] = 1
    write_json(directory, 'desktop_sync_receipt.json', receipt)
    write_json(directory, 'report.json', report)
    write_json(directory, 'pointer.json', {'report': str(directory/'report.json'),
                                          'report_sha256': verifier.sha(directory/'report.json')})
    with pytest.raises(ValueError, match='shared resume belongs to a separate delivery'):
        verifier.verify_current_delivery(directory/'pointer.json')


@pytest.mark.parametrize('state', ['absent', 'present', 'stale', 'explicit_missing'])
def test_preflight_executes_delivery_gates_and_blocks_stale_evidence(tmp_path, monkeypatch, state):
    monkeypatch.setattr(preflight_git, 'ROOT', tmp_path)
    pointer = tmp_path / 'artifacts/medagent_upgrade_20261013/latest_document_delivery.json'
    if state in ('present', 'stale'):
        pointer.parent.mkdir(parents=True)
        pointer.write_text('{}', encoding='utf-8')
    calls = []
    def run(command):
        calls.append(command)
        failed = '--delivery-pointer' in command and state in ('stale', 'explicit_missing')
        return SimpleNamespace(returncode=int(failed), stdout='{}', stderr='delivery: drift' if failed else '')
    monkeypatch.setattr(preflight_git, 'run', run)
    report = preflight_git.Report()
    preflight_git.check_document_delivery(report, pointer if state == 'explicit_missing' else None)
    assert '--repository-documents' in calls[0]
    assert len(calls) == (1 if state == 'absent' else 2)
    assert bool(report.errors) == (state in ('stale', 'explicit_missing'))
    if state == 'absent':
        assert '桌面交付未核验' in report.passed[0]


def test_ci_executes_portable_delivery_verifier():
    workflow = Path(__file__).resolve().parents[1] / '.github/workflows/verify.yml'
    assert 'python scripts/verify_delivery_evidence.py --repository-documents' in workflow.read_text(encoding='utf-8')
