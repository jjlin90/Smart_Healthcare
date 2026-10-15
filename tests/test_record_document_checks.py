import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import record_document_checks as recorder


@pytest.mark.parametrize('kind,expected', [('assertion', 'AssertionError'), ('pytest_raises', 'Failed')])
def test_mutation_gate_accepts_assertion_and_missing_expected_exception(kind, expected):
    def check():
        if kind == 'assertion':
            raise AssertionError('regression rejected mutation')
        with pytest.raises(RuntimeError):
            pass
    assert recorder.expect_regression_failure(check) == expected


def test_mutation_gate_rejects_undetected_mutation():
    with pytest.raises(AssertionError, match='Mutation was not rejected'):
        recorder.expect_regression_failure(lambda: None)


@pytest.mark.parametrize('error', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_mutation_gate_propagates_unrelated_errors_and_interrupts(error):
    def check():
        raise error('unexpected failure')
    with pytest.raises(error, match='unexpected failure'):
        recorder.expect_regression_failure(check)


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


@pytest.mark.parametrize('status', [0, 4])
def test_recorded_tests_use_and_clean_system_temp_with_real_child(repository, monkeypatch, status):
    # A tiny real child implements only the CLI arguments the recorder passes.
    # It exercises allocation, cleanup and evidence publication independently
    # from the outer pytest runner and without a nested full suite.
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    (repository / 'pytest.py').write_text(
        "from pathlib import Path\nimport sys\n"
        "base=Path(sys.argv[sys.argv.index('--basetemp')+1])\n"
        "assert not base.is_relative_to(Path.cwd())\n"
        "base.mkdir(parents=True)\n(base/'temporary.bin').write_bytes(b'temporary')\n"
        "report=Path(sys.argv[sys.argv.index('--junitxml')+1])\n"
        + "report.write_text('<testsuites><testsuite><testcase>"
        + ('<failure/>' if status else '')
        + "</testcase></testsuite></testsuites>',encoding='utf-8')\n"
        + f"print('fixture result');raise SystemExit({status})\n", encoding='utf-8')
    output = repository / 'artifacts/tests.log'
    assert recorder.record('tests', output) == status
    result = metadata(output)
    temporary = Path(result['pytest_temporary_root'])
    base = Path(result['command'][result['command'].index('--basetemp') + 1])
    assert not temporary.is_relative_to(repository)
    assert base.parent == temporary and result['temporary_data_cleaned'] is True
    assert not temporary.exists()
    assert result['test_counts']['total'] == 1 and result['inputs_unchanged'] is True
    assert output.with_suffix('.junit.xml').is_file()
    pointer = repository / 'artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json'
    assert pointer.is_file() == (status == 0)
    if status == 0:
        assert recorder.latest_test_validation()['test_counts']['passed'] == 1


@pytest.mark.parametrize('exception', [OSError, KeyboardInterrupt])
def test_interrupted_record_labels_abort_cleans_temp_and_preserves_success_pointer(repository, monkeypatch, exception):
    directory = repository / 'artifacts/medagent_upgrade_20261013'
    directory.mkdir()
    pointer = directory / 'latest_engineering_test_run.json'
    pointer.write_bytes(b'previous successful pointer')
    original_run = subprocess.run
    def interrupt(command, *args, **kwargs):
        if '-m' in command and 'pytest' in command:
            base = Path(command[command.index('--basetemp') + 1])
            base.mkdir(parents=True)
            (base / 'partial.bin').write_bytes(b'partial')
            raise exception('private exception detail')
        return original_run(command, *args, **kwargs)
    monkeypatch.setattr(recorder.subprocess, 'run', interrupt)
    output = repository / 'artifacts/aborted.log'
    with pytest.raises(exception):
        recorder.record('tests', output)
    result = metadata(output)
    assert result['status'] == 'aborted' and result['exit_code'] != 0
    assert result['temporary_data_cleaned'] is True
    assert not Path(result['pytest_temporary_root']).exists()
    assert not output.with_suffix('.junit.xml').exists()
    assert pointer.read_bytes() == b'previous successful pointer'
    assert 'private exception detail' not in output.read_text(encoding='utf-8')


@pytest.mark.parametrize('report_kind', ['empty', 'malformed', 'missing'])
def test_zero_exit_with_unusable_junit_labels_failure_and_preserves_pointer(repository, monkeypatch, report_kind):
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    actions = {'empty': "report.write_text('',encoding='utf-8')",
               'malformed': "report.write_text('<testsuites><',encoding='utf-8')",
               'missing': 'report.unlink()'}
    (repository / 'pytest.py').write_text(
        "from pathlib import Path\nimport sys\n"
        "base=Path(sys.argv[sys.argv.index('--basetemp')+1])\n"
        "base.mkdir(parents=True)\n(base/'partial.bin').write_bytes(b'partial')\n"
        "report=Path(sys.argv[sys.argv.index('--junitxml')+1])\n"
        + actions[report_kind] + "\nprint('child exited normally')\n", encoding='utf-8')
    directory = repository / 'artifacts/medagent_upgrade_20261013'
    directory.mkdir()
    pointer = directory / 'latest_engineering_test_run.json'
    pointer.write_bytes(b'previous successful pointer')
    output = repository / 'artifacts/unusable.log'
    assert invoke(monkeypatch, output, kind='tests') == 1
    result = metadata(output)
    assert result['status'] == 'aborted' and result['exit_code'] == 1
    assert result['child_exit_code'] == 0 and result['failure_stage'] == 'result_validation'
    assert result['temporary_data_cleaned'] is True
    assert not Path(result['pytest_temporary_root']).exists()
    assert pointer.read_bytes() == b'previous successful pointer'
    junit = output.with_suffix('.junit.xml')
    if report_kind == 'malformed':
        assert junit.read_bytes() == b'<testsuites><'
    else:
        assert not junit.exists()


def test_metadata_validation_error_is_recorded_after_successful_child(repository, monkeypatch):
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    (repository / 'pytest.py').write_text(
        "from pathlib import Path\nimport sys\n"
        "p=Path(sys.argv[sys.argv.index('--junitxml')+1])\n"
        "p.write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>',encoding='utf-8')\n",
        encoding='utf-8')
    original_hashes = recorder.hashes
    calls = []
    def fail_post_run(paths):
        calls.append(paths)
        if len(calls) == 3:
            raise OSError('private metadata detail')
        return original_hashes(paths)
    monkeypatch.setattr(recorder, 'hashes', fail_post_run)
    output = repository / 'artifacts/metadata_failure.log'
    assert invoke(monkeypatch, output, kind='tests') == 1
    result = metadata(output)
    assert result['status'] == 'aborted' and result['child_exit_code'] == 0
    assert result['failure_stage'] == 'input_validation'
    assert result['temporary_data_cleaned'] is True
    assert output.with_suffix('.junit.xml').is_file()
    assert not (repository / 'artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json').exists()
    assert 'private metadata detail' not in output.read_text(encoding='utf-8')


def test_setup_failure_is_labeled_and_removes_only_owned_empty_junit(repository, monkeypatch):
    def fail_allocation(*args, **kwargs):
        raise OSError('temporary directory unavailable')
    monkeypatch.setattr(recorder.tempfile, 'TemporaryDirectory', fail_allocation)
    output = repository / 'artifacts/setup_failure.log'
    assert invoke(monkeypatch, output, kind='tests') == 1
    result = metadata(output)
    assert result['status'] == 'aborted' and result['failure_stage'] == 'setup'
    assert result['child_exit_code'] is None and result['pytest_temporary_root'] is None
    assert not output.with_suffix('.junit.xml').exists()


def test_existing_junit_is_never_removed_by_setup_failure(repository, monkeypatch):
    output = repository / 'artifacts/existing_junit.log'
    junit = output.with_suffix('.junit.xml')
    junit.write_bytes(b'')
    assert invoke(monkeypatch, output, kind='tests') == 1
    assert junit.exists() and junit.read_bytes() == b''
    result = metadata(output)
    assert result['status'] == 'aborted' and result['failure_stage'] == 'setup'


def test_junit_counts_and_current_validation_reject_changed_code_or_counts(repository):
    directory = repository / 'artifacts/medagent_upgrade_20261013'
    directory.mkdir()
    report = directory / 'run.log'
    report.write_text('recorded run', encoding='utf-8')
    xml = directory / 'run.xml'
    xml.write_text('<testsuites><testsuite><testcase/><testcase/><testcase><skipped/></testcase></testsuite></testsuites>', encoding='utf-8')
    counts = recorder.test_counts(xml)
    assert counts == {'total': 3, 'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 1}
    pointer = directory / 'latest_engineering_test_run.json'
    code = repository / 'scripts/preflight_git.py'
    data = {'kind': 'tests', 'exit_code': 0, 'inputs_unchanged': True, 'test_counts': counts,
            'report': str(report), 'report_sha256': recorder.hashes([report])[str(report)],
            'junit_report': str(xml), 'junit_sha256': recorder.hashes([xml])[str(xml)],
            'input_sha256': recorder.hashes(recorder.candidates()),
            'code_sha256': recorder.hashes([code])}
    pointer.write_text(json.dumps(data), encoding='utf-8')
    validation = recorder.latest_test_validation()
    assert validation['validation_scope'] == 'code_and_dependencies'
    assert validation['recorded_inputs_match_current'] is True
    readme = repository / 'README.md'
    before = readme.read_bytes()
    readme.write_text('# Regenerated document\n', encoding='utf-8')
    validation = recorder.latest_test_validation()
    assert validation['test_counts'] == counts
    assert validation['recorded_inputs_match_current'] is False
    readme.write_bytes(before)
    data['test_counts'] = {**counts, 'passed': 480}
    pointer.write_text(json.dumps(data), encoding='utf-8')
    assert recorder.latest_test_validation() is None
    data['test_counts'] = counts
    pointer.write_text(json.dumps(data), encoding='utf-8')
    code.write_text('# changed code', encoding='utf-8')
    assert recorder.latest_test_validation() is None


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
    assert result['worktree_dirty'] is True
    assert result['worktree_status_sha256']
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
    for name in ('MedAgent_项目理解指南.md', 'MedAgent_AI_多Agent项目话术.md',
                 'MedAgent_文档对照与工程改进_2026-10-13.md'):
        (desktop / name).write_text('# Desktop\n', encoding='utf-8')
    (desktop / 'MedAgent_AI_多Agent项目/03-Agent.md').write_text('# Agent\n', encoding='utf-8')
    nested = desktop / 'MedAgent_AI_多Agent项目/专题/术语.md'
    nested.parent.mkdir()
    nested.write_text('# Nested\n', encoding='utf-8')
    extra = desktop / '工程对照.md'
    extra.write_text('# 工程对照\n', encoding='utf-8')
    output = repository / 'artifacts/links.json'
    monkeypatch.setattr(sys, 'argv', ['record_document_checks.py', '--kind', 'links',
                                    '--desktop', str(desktop), '--extra-document', str(extra),
                                    '--extra-document', str(extra), '--output', str(output)])
    assert recorder.main() == 0
    result = metadata(output)
    assert result['files'] == len(result['input_sha256']) == 7
    assert len(result['document_files']) == 7
    assert str(nested.resolve()) in result['input_sha256']
    assert str(extra.resolve()) in result['input_sha256']
    assert result['document_files'] == [{'path': p, 'sha256': h}
                                       for p, h in result['input_sha256'].items()]
    assert result['run_id'] and result['head'] == result['head_after']
    assert '--extra-document' in result['command']
    assert result['issues'] == [] and result['inputs_unchanged'] is True
    assert result['generated_at_utc'] == result['finished_at_utc']
    assert result['recorder_command'][-2:] == ['--output', str(output)]


def test_desktop_scope_includes_engineering_by_default_and_rejects_missing_file(repository):
    desktop = repository / 'artifacts/desktop'
    (desktop / 'MedAgent_AI_多Agent项目').mkdir(parents=True)
    for name in ('MedAgent_项目理解指南.md', 'MedAgent_AI_多Agent项目话术.md'):
        (desktop / name).write_text('# Desktop\n', encoding='utf-8')
    with pytest.raises(ValueError, match='required desktop document is missing'):
        recorder.documents(desktop)
    engineering = desktop / 'MedAgent_文档对照与工程改进_2026-10-13.md'
    engineering.write_text('# 工程\n', encoding='utf-8')
    assert engineering.resolve() in recorder.documents(desktop)
    assert recorder.documents(desktop, [engineering]) == recorder.documents(desktop)


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
