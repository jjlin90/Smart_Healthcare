"""Record preflight, document links or existing desktop bytes with run metadata."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote
import uuid
import time
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.preflight_git import _markdown_headings, _markdown_without_fences


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def observe_git_state() -> dict:
    """Read a dated local snapshot without assuming concurrent Git work stops.

    The tracking reference is local metadata, never a remote-server query.
    A changed HEAD makes the observation non-atomic; it is recorded, not rejected.
    """
    started = datetime.now(timezone.utc).isoformat()
    head_before = git('rev-parse', 'HEAD')
    try:
        origin_main = git('rev-parse', '--verify', '--quiet', 'refs/remotes/origin/main')
    except subprocess.CalledProcessError:
        origin_main = None
    status = git('status', '--porcelain')
    head_after = git('rev-parse', 'HEAD')
    return {'observation_started_at_utc': started,
            'observation_finished_at_utc': datetime.now(timezone.utc).isoformat(),
            'local_head_before': head_before, 'local_head_after': head_after,
            'local_origin_main_tracking_ref': origin_main,
            'working_tree_status_at_observation': status,
            'head_unchanged_while_observing': head_before == head_after,
            'remote_server_queried': False,
            'scope': 'Dated local observation only; later commits and pushes may change this state.'}


def candidates() -> list[Path]:
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z')
    return sorted({ROOT / name for name in names.split('\0') if name})


def hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def is_code_dependency(path: Path) -> bool:
    return path.suffix.lower() == '.py' or path.name in (
        'requirements.txt', 'requirements-dev.txt', 'constraints.txt', 'pyproject.toml')


def expect_regression_failure(check) -> str:
    """Detect assertion failures, including pytest.raises' Failed outcome.

    A successful check means the mutation escaped detection. Unrelated errors
    and process interrupts must propagate rather than count as detection.
    """
    import pytest
    try:
        check()
    except (AssertionError, pytest.fail.Exception) as exc:
        return type(exc).__name__
    raise AssertionError("Mutation was not rejected by the regression check")


def test_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    cases = root.findall('.//testcase')
    counts = {'total': len(cases), 'failed': 0, 'errors': 0, 'skipped': 0}
    for case in cases:
        for name, tag in [('failed', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]:
            counts[name] += int(case.find(tag) is not None)
    counts['passed'] = counts['total'] - sum(counts[name] for name in ('failed', 'errors', 'skipped'))
    return counts


def latest_test_validation() -> dict | None:
    """Validate the code regression; regenerated documents need separate checks."""
    pointer = ROOT / 'artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json'
    if not pointer.is_file():
        return None
    try:
        data = json.loads(pointer.read_text(encoding='utf-8'))
        report = Path(data['report'])
        xml = Path(data['junit_report'])
        paths = candidates()
        current_inputs = hashes(paths)
        current_code = {str(p.resolve()): current_inputs[str(p.resolve())] for p in paths
                        if is_code_dependency(p)
                        and str(p.resolve()) in current_inputs}
        if (data['kind'] != 'tests' or data['exit_code'] != 0 or not data['inputs_unchanged']
                or data['test_counts']['total'] < 1
                or data['report_sha256'] != hashes([report]).get(str(report.resolve()))
                or data['junit_sha256'] != hashes([xml]).get(str(xml.resolve()))
                or data['test_counts'] != test_counts(xml)
                or data['code_sha256'] != current_code):
            return None
        frozen_inputs = data.get('input_sha256', {})
        if not isinstance(frozen_inputs, dict):
            return None
        differences = {
            'added': sorted(current_inputs.keys() - frozen_inputs.keys()),
            'removed': sorted(frozen_inputs.keys() - current_inputs.keys()),
            'changed': sorted(p for p in frozen_inputs.keys() & current_inputs.keys()
                              if frozen_inputs[p] != current_inputs[p]),
        }
        return {**data, 'validation_scope': 'code_and_dependencies',
                'recorded_inputs_match_current': frozen_inputs == current_inputs,
                'recorded_input_differences': differences}
    except (ValueError, KeyError, OSError, TypeError, ET.ParseError):
        return None


def documents(desktop: Path | None, extra_documents: list[Path] | None = None) -> list[Path]:
    paths = [p for p in candidates() if p.suffix.lower() == '.md' and p.is_file()]
    if desktop is not None:
        paths += [desktop / 'MedAgent_项目理解指南.md',
                  desktop / 'MedAgent_AI_多Agent项目话术.md',
                  desktop / 'MedAgent_文档对照与工程改进_2026-10-13.md']
        directory = desktop / 'MedAgent_AI_多Agent项目'
        if not directory.is_dir():
            raise ValueError('desktop document directory is missing')
        paths += sorted(p for p in directory.rglob('*') if p.is_file() and p.suffix.lower() == '.md')
        if any(not p.is_file() for p in paths):
            raise ValueError('required desktop document is missing')
    for path in extra_documents or []:
        if not path.is_file() or path.suffix.lower() != '.md':
            raise ValueError('extra document must be an existing Markdown file')
        paths.append(path)
    return sorted(set(p.resolve() for p in paths))


def check_links(paths: list[Path]) -> dict:
    issues, urls = [], set()
    heading_cache = {}
    for path in paths:
        body = _markdown_without_fences(path.read_text(encoding='utf-8-sig'))
        body = re.sub(r'(`+)[^\n]*?\1', lambda m: ' ' * len(m[0]), body)
        links = [(m.start(), m[1], False) for m in re.finditer(
            r'\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+"[^"\n]*")?\s*\)', body)]
        links += [(m.start(), m[1].split('|', 1)[0], True)
                  for m in re.finditer(r'\[\[([^\]\n]+)\]\]', body)]
        for position, raw, wiki in links:
            raw = raw.strip().strip('<>')
            if raw.startswith(('http://', 'https://')):
                urls.add(raw)
                continue
            # Drive letters are local paths, not URL schemes.
            if not re.match(r'^[A-Za-z]:[/\\]', raw) and (
                re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', raw) or raw.startswith('//')
            ):
                continue
            dest, _, anchor = unquote(raw).partition('#')
            if wiki and dest and not Path(dest).suffix:
                dest += '.md'
            target = (path.parent / dest).resolve() if dest else path
            reason = None
            if not target.is_file():
                reason = 'missing_file'
            elif anchor and target.suffix.lower() == '.md':
                if target not in heading_cache:
                    heading_cache[target] = _markdown_headings(target.read_text(encoding='utf-8-sig'))
                titles, anchors = heading_cache[target]
                if anchor not in (titles if wiki else anchors):
                    reason = 'missing_anchor'
            if reason:
                issues.append({'file': str(path), 'line': body[:position].count('\n') + 1,
                               'target': raw, 'issue': reason})
    return {'files': len(paths), 'issues': issues, 'external_urls': sorted(urls)}


def checked_output_path(output: Path) -> Path:
    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('output must be inside the repository ignored artifacts directory')
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', '--', str(output)], cwd=ROOT)
    if ignored.returncode != 0 or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('output must be ignored under artifacts/')
    return output


def record_git_observation(output: Path) -> int:
    """Write one dated local Git observation to a new JSON file."""
    output = checked_output_path(output)
    observation = observe_git_state()
    data = {**observation, 'kind': 'git-observation', 'run_id': str(uuid.uuid4()),
            'command': [sys.executable, str(Path(__file__).resolve()), '--kind',
                        'git-observation', '--output', str(output)]}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'kind': data['kind'], 'run_id': data['run_id']}, ensure_ascii=False))
    return 0


def record(kind: str, output: Path, desktop: Path | None = None,
           extra_documents: list[Path] | None = None) -> int:
    output = checked_output_path(output)
    # Reserve the filename before executing; existing evidence is never overwritten.
    with output.open('x', encoding='utf-8', newline='\n') as stream, ExitStack() as cleanup:
        started = datetime.now(timezone.utc).isoformat()
        timer = time.monotonic()
        head, command, text = None, [], ''
        junit, counts, temporary_root = None, None, None
        junit_reserved, child_exit_code = False, None
        failure_stage = 'setup'
        try:
            head = git('rev-parse', 'HEAD')
            worktree_status = git('status', '--porcelain')
            paths = candidates() if kind in ('quick', 'tests') else documents(desktop, extra_documents)
            before = hashes(paths)
            sources = [Path(__file__).resolve(), ROOT / 'scripts/preflight_git.py']
            source_before = hashes(sources)
            command = [sys.executable, str(ROOT / 'scripts/preflight_git.py'), '--quick']
            if kind == 'tests':
                junit = output.with_suffix('.junit.xml')
                with junit.open('x', encoding='utf-8'):
                    pass  # Reserve this unique path; never overwrite old evidence.
                junit_reserved = True
                temporary_root = Path(cleanup.enter_context(
                    tempfile.TemporaryDirectory(prefix='medagent-record-tests-'))).resolve()
                if (temporary_root.is_relative_to(ROOT.resolve()) or
                        temporary_root.parent != Path(tempfile.gettempdir()).resolve()):
                    raise ValueError('pytest temporary directory must be in the system temp directory outside the repository')
                command = [sys.executable, '-m', 'pytest', '-p', 'pytest_asyncio.plugin', '-p', 'anyio.pytest_plugin',
                           '-p', 'no:cacheprovider', '--basetemp', str(temporary_root / 'pytest'),
                           '--junitxml', str(junit), '-q']
            if kind in ('quick', 'tests'):
                failure_stage = 'execution'
                result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, encoding='utf-8',
                                        env={**os.environ, 'PYTHONIOENCODING': 'utf-8',
                                             **({'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'} if kind == 'tests' else {})})
                child_exit_code = result.returncode
                code, text = result.returncode, result.stdout
                failure_stage = 'result_validation'
                report = None
                if kind == 'tests':
                    counts = test_counts(junit)
                    code = code or int(counts['total'] < 1 or bool(counts['failed'] or counts['errors']))
            else:
                report = check_links(paths)
                code, text = int(bool(report['issues'])), ''
                command = [sys.executable, str(Path(__file__).resolve()), '--kind', 'links',
                           '--output', str(output)]
                if desktop is not None:
                    command += ['--desktop', str(desktop.resolve())]
                for path in extra_documents or []:
                    command += ['--extra-document', str(path.resolve())]
            failure_stage = 'cleanup'
            cleanup.close()
            failure_stage = 'input_validation'
            after_paths = candidates() if kind in ('quick', 'tests') else documents(desktop, extra_documents)
            head_after = git('rev-parse', 'HEAD')
            stable = (before == hashes(after_paths) and head == head_after
                      and source_before == hashes(sources))
            code = code or int(not stable)
            metadata = {'run_id': str(uuid.uuid4()), 'kind': kind, 'started_at_utc': started,
                        'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                        'head': head, 'head_after': head_after, 'cwd': str(ROOT),
                        'worktree_dirty': bool(worktree_status),
                        'worktree_status_sha256': hashlib.sha256(worktree_status.encode()).hexdigest(),
                        'snapshot_basis': 'input_sha256 freezes workspace bytes at run start; code_sha256 scopes code regression. Later document generation is verified separately; HEAD alone does not describe uncommitted contents.',
                        'command': command, 'recorder_command': [sys.executable, *sys.argv], 'exit_code': code, 'inputs_unchanged': stable,
                        'input_sha256': before, 'checker_source_sha256': source_before}
            metadata['elapsed_seconds'] = round(time.monotonic() - timer, 3)
            if kind == 'tests':
                metadata.update(test_counts=counts, junit_report=str(junit), junit_sha256=hashes([junit])[str(junit.resolve())])
                metadata.update(pytest_temporary_root=str(temporary_root),
                                temporary_data_cleaned=not temporary_root.exists())
            failure_stage = 'record_write'
            if report is None:
                stream.write(text + '\n[RUN] ' + json.dumps(metadata, ensure_ascii=False) + '\n')
            else:
                report.update(metadata)
                report['generated_at_utc'] = metadata['finished_at_utc']
                report['document_files'] = [{'path': path, 'sha256': digest}
                                            for path, digest in before.items()]
                report['extra_documents'] = [str(path.resolve()) for path in extra_documents or []]
                stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        except (OSError, KeyboardInterrupt, ET.ParseError, ValueError, TypeError,
                KeyError, subprocess.SubprocessError) as exc:
            cleanup_error = None
            try:
                cleanup.close()
            except OSError as cleanup_exc:
                cleanup_error = type(cleanup_exc).__name__
            incomplete_junit = {}
            try:
                if junit_reserved and junit is not None and junit.is_file():
                    if junit.stat().st_size == 0:
                        junit.unlink()  # Only this run's exclusively reserved empty file.
                    else:
                        incomplete_junit = {'incomplete_junit_report': str(junit),
                                            'incomplete_junit_sha256': hashlib.sha256(junit.read_bytes()).hexdigest()}
            except OSError as cleanup_exc:
                cleanup_error = type(cleanup_exc).__name__
            aborted = {'run_id': str(uuid.uuid4()), 'kind': kind, 'status': 'aborted',
                       'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                       'head': head, 'cwd': str(ROOT), 'command': command,
                       'exit_code': 130 if isinstance(exc, KeyboardInterrupt) else 1,
                       'child_exit_code': child_exit_code, 'failure_stage': failure_stage,
                       'aborted_exception_type': type(exc).__name__, 'inputs_unchanged': False,
                       'pytest_temporary_root': str(temporary_root) if temporary_root else None,
                       'temporary_data_cleaned': temporary_root is None or not temporary_root.exists(),
                       'cleanup_exception_type': cleanup_error}
            aborted.update(incomplete_junit)
            stream.seek(0)
            stream.truncate()
            if kind == 'links':
                stream.write(json.dumps(aborted, ensure_ascii=False, indent=2) + '\n')
            else:
                stream.write(text + '\nRecording aborted before complete validated evidence.\n[RUN] '
                             + json.dumps(aborted, ensure_ascii=False) + '\n')
            stream.flush()
            raise
    if kind == 'tests' and code == 0:
        pointer = ROOT / 'artifacts/medagent_upgrade_20261013/latest_engineering_test_run.json'
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps({**metadata, 'validation_scope': 'code_and_dependencies', 'report': str(output),
            'report_sha256': hashes([output])[str(output)],
            'code_sha256': {path: digest for path, digest in before.items()
                if is_code_dependency(Path(path))}
            }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'exit_code': code, 'run_id': metadata['run_id'],
                      'inputs_unchanged': stable}, ensure_ascii=False))
    return code


def record_desktop_snapshot(output: Path, receipt_path: Path, manifest_path: Path) -> int:
    """Capture existing targets into a new ignored directory; never edit the desktop."""
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('snapshot output must be under artifacts/')
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', '--', str(output)], cwd=ROOT)
    if ignored.returncode:
        raise ValueError('snapshot output must be ignored')
    baseline_paths = [receipt_path, manifest_path]
    baseline_data = {path: path.read_bytes() for path in baseline_paths}
    baselines_before = {str(path.resolve()): hashlib.sha256(value).hexdigest() for path, value in baseline_data.items()}
    receipt = json.loads(baseline_data[receipt_path].decode('utf-8-sig'))
    manifest = json.loads(baseline_data[manifest_path].decode('utf-8-sig'))
    if not isinstance(receipt, dict):
        raise ValueError('snapshot: invalid receipt')
    entries = receipt.get('entries', [])
    if not isinstance(entries, list) or not entries or receipt.get('count') != len(entries) or not isinstance(manifest, list) or not manifest:
        raise ValueError('snapshot: invalid receipt or manifest')
    targets = [Path(item['target']).resolve() for item in entries]
    projected = [Path(item['target']).resolve() for item in manifest]
    if len(set(targets)) != len(targets) or len(set(projected)) != len(projected):
        raise ValueError('snapshot: duplicate targets')
    if not set(projected) <= set(targets):
        raise ValueError('snapshot: manifest target missing from receipt')
    if any(not path.is_file() for path in targets):
        raise ValueError('snapshot: target file is missing')
    output.mkdir()  # Exclusive directory creation also protects every existing report.
    (output / 'snapshots').mkdir()
    started = datetime.now(timezone.utc).isoformat()
    head = git('rev-parse', 'HEAD')
    checker_before = hashes([Path(__file__).resolve()])
    data = {target: target.read_bytes() for target in targets}
    before = {str(path): hashlib.sha256(value).hexdigest() for path, value in data.items()}
    new_manifest = []
    for index, item in enumerate(manifest, 1):
        target = Path(item['target']).resolve()
        source = output / 'snapshots' / f'source_{index}{target.suffix.lower()}'
        source.write_bytes(data[target])
        new_manifest.append({'source': str(source), 'target': str(target),
                             'before_sha256': before[str(target)], 'after_sha256': before[str(target)],
                             'previous_recorded_sha256': item['after_sha256'],
                             'operation': 'snapshot_existing_desktop_bytes'})
    head_after = git('rev-parse', 'HEAD')
    stable = (before == hashes(targets) and head == head_after
              and baselines_before == hashes(baseline_paths)
              and checker_before == hashes([Path(__file__).resolve()]))
    metadata = {'run_id': str(uuid.uuid4()), 'kind': 'desktop-snapshot',
                'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                'head': head, 'head_after': head_after, 'cwd': str(ROOT),
                'command': [sys.executable, str(Path(__file__).resolve()), '--kind', 'desktop-snapshot',
                            '--output', str(output), '--receipt', str(receipt_path.resolve()),
                            '--manifest', str(manifest_path.resolve())],
                'exit_code': int(not stable), 'inputs_unchanged': stable,
                'input_sha256': before, 'baseline_sha256': baselines_before,
                'checker_source_sha256': checker_before,
                'operation': 'read_current_desktop_files_without_modifying_them',
                'entries': new_manifest}
    new_entries = []
    for item in entries:
        target = str(Path(item['target']).resolve())
        new_entries.append({**item, 'target': target, 'sha256': before[target],
                            'previous_recorded_sha256': item['sha256'],
                            'previous_source_receipt': item.get('source_receipt'),
                            'source_receipt': str(output / 'desktop_snapshot.json'), 'verified': stable})
    new_receipt = {'verified_at_utc': metadata['finished_at_utc'], 'count': len(entries),
                   'scope': 'Current target hashes read without editing desktop files; declared manifest subset captured.',
                   'entries': new_entries}
    for name, value in [('desktop_snapshot.json', metadata), ('desktop_manifest.json', new_manifest),
                        ('latest_desktop_receipt.json', new_receipt)]:
        with (output / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(output), 'exit_code': metadata['exit_code'],
                      'run_id': metadata['run_id'], 'inputs_unchanged': stable}, ensure_ascii=False))
    return metadata['exit_code']


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('quick', 'links', 'tests', 'desktop-snapshot', 'git-observation'), required=True)
    parser.add_argument('--output', type=Path, required=True, help='new file, or new directory for desktop-snapshot, under ignored artifacts/')
    parser.add_argument('--desktop', type=Path, help='optional desktop directory for links mode')
    parser.add_argument('--extra-document', type=Path, action='append', default=[],
                        help='additional Markdown file explicitly included in links mode; repeatable')
    parser.add_argument('--receipt', type=Path, help='existing target receipt for desktop-snapshot')
    parser.add_argument('--manifest', type=Path, help='existing declared subset for desktop-snapshot')
    args = parser.parse_args()
    if args.desktop is not None and args.kind != 'links':
        parser.error('--desktop requires --kind links')
    if args.extra_document and args.kind != 'links':
        parser.error('--extra-document requires --kind links')
    if args.kind == 'desktop-snapshot' and (args.receipt is None or args.manifest is None):
        parser.error('desktop-snapshot requires --receipt and --manifest')
    if args.kind != 'desktop-snapshot' and (args.receipt is not None or args.manifest is not None):
        parser.error('--receipt/--manifest require --kind desktop-snapshot')
    try:
        if args.kind == 'git-observation':
            return record_git_observation(args.output)
        if args.kind == 'desktop-snapshot':
            return record_desktop_snapshot(args.output, args.receipt, args.manifest)
        return record(args.kind, args.output, args.desktop, args.extra_document)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, subprocess.SubprocessError) as exc:
        print(f'Recording failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
