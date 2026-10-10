import hashlib
import json
from pathlib import Path

import pytest

from scripts import verify_delivery_evidence as verifier


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
