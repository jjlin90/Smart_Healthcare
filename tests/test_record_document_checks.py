import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import record_document_checks as recorder


@pytest.fixture
def repository(tmp_path, monkeypatch):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / '.gitignore').write_text('artifacts/\n', encoding='utf-8')
    (tmp_path / 'README.md').write_text('# Main\n# Main\n[valid](#main-1)\n', encoding='utf-8')
    (tmp_path / 'scripts/preflight_git.py').write_text("print('[PASS] fixture check')\n", encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Recorder test',
                    '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    monkeypatch.setattr(recorder, 'ROOT', tmp_path)
    return tmp_path


def invoke(monkeypatch, output, kind='quick'):
    monkeypatch.setattr(sys, 'argv', ['record_document_checks.py', '--kind', kind,
                                    '--output', str(output)])
    return recorder.main()


def metadata(path):
    body = path.read_text(encoding='utf-8')
    return json.loads(body.split('\n[RUN] ')[1]) if path.suffix == '.log' else json.loads(body)


@pytest.mark.parametrize('status', [0, 7])
def test_quick_records_real_child_status_and_input_hashes(repository, monkeypatch, status):
    tool = repository / 'scripts/preflight_git.py'
    tool.write_text(f"print('[PASS] fixture check')\nraise SystemExit({status})\n", encoding='utf-8')
    output = repository / 'artifacts/quick.log'
    assert invoke(monkeypatch, output) == status
    result = metadata(output)
    assert result['exit_code'] == status and result['inputs_unchanged'] is True
    assert result['command'][-2:] == [str(tool), '--quick']
    assert result['started_at_utc'] <= result['finished_at_utc']
    assert result['head'] == result['head_after']
    assert len(result['input_sha256']) == 3
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
               for p, h in result['input_sha256'].items())
    assert '[PASS] fixture check' in output.read_text(encoding='utf-8')


@pytest.mark.parametrize('change', ['document', 'new_candidate', 'checker', 'head'])
def test_quick_rejects_input_or_head_changes_even_when_child_succeeds(repository, monkeypatch, change):
    actions = {
        'document': "Path('README.md').write_text('# Changed\\n', encoding='utf-8')",
        'new_candidate': "Path('new.md').write_text('# New\\n', encoding='utf-8')",
        'checker': "Path(__file__).write_text('# Changed checker\\n', encoding='utf-8')",
        'head': "subprocess.run(['git', '-c', 'user.name=Recorder test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'during check'], check=True)",
    }
    (repository / 'scripts/preflight_git.py').write_text(
        'from pathlib import Path\nimport subprocess\n' + actions[change] + '\n', encoding='utf-8')
    output = repository / 'artifacts/drift.log'
    assert invoke(monkeypatch, output) == 1
    result = metadata(output)
    assert result['exit_code'] == 1 and result['inputs_unchanged'] is False


@pytest.mark.parametrize('location', ['existing', 'outside', 'not_ignored', 'missing_parent'])
def test_cli_rejects_unsafe_outputs_without_overwriting(repository, tmp_path, monkeypatch, capsys, location):
    paths = {'existing': repository / 'artifacts/existing.log',
             'outside': repository.parent / (repository.name + '-outside.log'),
             'not_ignored': repository / 'unignored.log',
             'missing_parent': repository / 'artifacts/absent/output.log'}
    output = paths[location]
    if location == 'existing':
        output.write_bytes(b'preserve evidence')
    assert invoke(monkeypatch, output) == 1
    message = capsys.readouterr().err
    expected = {'existing': 'File exists', 'outside': 'output must be inside',
                'not_ignored': 'output must be ignored', 'missing_parent': 'No such file'}
    assert expected[location] in message
    if location == 'existing':
        assert output.read_bytes() == b'preserve evidence'
    else:
        assert not output.exists()


def test_links_records_specific_issues_and_separates_source_hashes(repository, monkeypatch):
    (repository / 'README.md').write_text(
        '# Main\n# Main\n[valid](#main-1)\n[missing](absent.md)\n'
        '[anchor](#absent)\n`[example](ignored.md)`\n'
        '```md\n[example](ignored2.md)\n```\n[external](https://example.invalid/)\n', encoding='utf-8')
    output = repository / 'artifacts/links.json'
    assert invoke(monkeypatch, output, 'links') == 1
    result = metadata(output)
    assert result['files'] == len(result['input_sha256']) == 1
    assert len(result['checker_source_sha256']) == 2
    assert [(x['target'], x['issue']) for x in result['issues']] == [
        ('absent.md', 'missing_file'), ('#absent', 'missing_anchor')]
    assert result['external_urls'] == ['https://example.invalid/']
    assert result['inputs_unchanged'] is True


def test_links_success_metadata_and_desktop_scope(repository, monkeypatch):
    desktop = repository / 'artifacts/desktop'
    (desktop / 'MedAgent_AI_多Agent项目').mkdir(parents=True)
    for name in ('MedAgent_项目理解指南.md', 'MedAgent_AI_多Agent项目话术.md'):
        (desktop / name).write_text('# Desktop\n', encoding='utf-8')
    (desktop / 'MedAgent_AI_多Agent项目/03-Agent.md').write_text('# Agent\n', encoding='utf-8')
    output = repository / 'artifacts/links.json'
    monkeypatch.setattr(sys, 'argv', ['record_document_checks.py', '--kind', 'links',
                                    '--desktop', str(desktop), '--output', str(output)])
    assert recorder.main() == 0
    result = metadata(output)
    assert result['files'] == len(result['input_sha256']) == 4
    assert result['issues'] == [] and result['inputs_unchanged'] is True
    assert result['generated_at_utc'] == result['finished_at_utc']
    assert result['recorder_command'][-2:] == ['--output', str(output)]


def test_preflight_requires_recorder_and_its_regressions():
    from scripts import preflight_git
    paths = [preflight_git.ROOT / name for name in preflight_git.REQUIRED_TRACKED_PATHS]
    for omitted in ('scripts/record_document_checks.py', 'tests/test_record_document_checks.py'):
        report = preflight_git.Report()
        preflight_git.check_required_project_files_tracked(
            report, [path for path in paths if path != preflight_git.ROOT / omitted])
        assert report.errors == ['实现或复现所需文件尚未加入 Git 索引：' + omitted]
        assert not report.passed


@pytest.fixture
def desktop_baseline(repository):
    desktop = repository / 'artifacts/desktop'
    desktop.mkdir()
    targets = [desktop / 'primary.md', desktop / 'other.html']
    for path in targets:
        path.write_text('# Current desktop\n', encoding='utf-8')
    entries = [{'target': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in targets]
    receipt = repository / 'artifacts/old_receipt.json'
    receipt.write_text(json.dumps({'count': len(entries), 'entries': entries}), encoding='utf-8')
    manifest = repository / 'artifacts/old_manifest.json'
    manifest.write_text(json.dumps([{'target': entries[0]['target'], 'after_sha256': entries[0]['sha256']}]), encoding='utf-8')
    return receipt, manifest, targets


def test_desktop_snapshot_records_source_bytes_without_modifying_targets(repository, desktop_baseline):
    receipt, manifest, targets = desktop_baseline
    targets[0].write_text('# A later edit\n', encoding='utf-8')
    before = {path: path.read_bytes() for path in targets}
    output = repository / 'artifacts/new_snapshot'
    assert recorder.record_desktop_snapshot(output, receipt, manifest) == 0
    snapshot = json.loads((output / 'desktop_snapshot.json').read_text(encoding='utf-8'))
    merged = json.loads((output / 'latest_desktop_receipt.json').read_text(encoding='utf-8'))
    projection = json.loads((output / 'desktop_manifest.json').read_text(encoding='utf-8'))
    assert snapshot['operation'] == 'read_current_desktop_files_without_modifying_them'
    assert snapshot['inputs_unchanged'] is True and len(snapshot['baseline_sha256']) == 2
    assert len(merged['entries']) == merged['count'] == len(snapshot['input_sha256']) == 2
    assert Path(projection[0]['source']).name == 'source_1.md'
    assert Path(projection[0]['source']).read_bytes() == before[targets[0]]
    assert projection[0]['previous_recorded_sha256'] != projection[0]['after_sha256']
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize('change,reason', [
    ('duplicate', 'snapshot: duplicate targets'),
    ('not_in_receipt', 'snapshot: manifest target missing from receipt'),
    ('missing_file', 'snapshot: target file is missing'),
])
def test_desktop_snapshot_rejects_invalid_baselines(repository, desktop_baseline, change, reason):
    receipt, manifest, targets = desktop_baseline
    if change == 'duplicate':
        value = json.loads(receipt.read_text(encoding='utf-8'))
        value['entries'].append(value['entries'][0])
        value['count'] += 1
        receipt.write_text(json.dumps(value), encoding='utf-8')
    elif change == 'not_in_receipt':
        manifest.write_text(json.dumps([{'target': str(repository / 'absent.md'), 'after_sha256': 'old'}]), encoding='utf-8')
    else:
        targets[0].unlink()
    output = repository / 'artifacts/invalid_snapshot'
    with pytest.raises(ValueError, match=reason):
        recorder.record_desktop_snapshot(output, receipt, manifest)
    assert not output.exists()


def test_desktop_snapshot_rejects_target_change_during_capture(repository, desktop_baseline, monkeypatch):
    receipt, manifest, targets = desktop_baseline
    original = recorder.hashes

    def changed(paths):
        if paths == [path.resolve() for path in targets]:
            targets[0].write_text('# Changed during capture\n', encoding='utf-8')
        return original(paths)

    monkeypatch.setattr(recorder, 'hashes', changed)
    output = repository / 'artifacts/drift_snapshot'
    assert recorder.record_desktop_snapshot(output, receipt, manifest) == 1
    snapshot = json.loads((output / 'desktop_snapshot.json').read_text(encoding='utf-8'))
    assert snapshot['exit_code'] == 1 and snapshot['inputs_unchanged'] is False
    receipt_value = json.loads((output / 'latest_desktop_receipt.json').read_text(encoding='utf-8'))
    assert all(item['verified'] is False for item in receipt_value['entries'])


def test_desktop_snapshot_preserves_existing_directory(repository, desktop_baseline):
    receipt, manifest, _ = desktop_baseline
    output = repository / 'artifacts/existing_snapshot'
    output.mkdir()
    evidence = output / 'evidence.txt'
    evidence.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        recorder.record_desktop_snapshot(output, receipt, manifest)
    assert evidence.read_bytes() == b'preserve'
    assert list(output.iterdir()) == [evidence]


def test_desktop_snapshot_cli_requires_explicit_baselines(repository, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['record_document_checks.py', '--kind', 'desktop-snapshot',
                                    '--output', str(repository / 'artifacts/snapshot')])
    with pytest.raises(SystemExit) as raised:
        recorder.main()
    assert raised.value.code == 2
    assert 'desktop-snapshot requires --receipt and --manifest' in capsys.readouterr().err
