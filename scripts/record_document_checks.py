"""Record preflight, document links or existing desktop bytes with run metadata."""
from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.preflight_git import _markdown_headings, _markdown_without_fences


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def candidates() -> list[Path]:
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z')
    return sorted({ROOT / name for name in names.split('\0') if name})


def hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def documents(desktop: Path | None) -> list[Path]:
    paths = [p for p in candidates() if p.suffix.lower() == '.md' and p.is_file()]
    if desktop is not None:
        paths += [desktop / 'MedAgent_项目理解指南.md',
                  desktop / 'MedAgent_AI_多Agent项目话术.md']
        directory = desktop / 'MedAgent_AI_多Agent项目'
        if not directory.is_dir():
            raise ValueError('desktop document directory is missing')
        paths += sorted(directory.glob('*.md'))
        if any(not p.is_file() for p in paths):
            raise ValueError('required desktop document is missing')
    return sorted(set(p.resolve() for p in paths))


def check_links(paths: list[Path]) -> dict:
    issues, urls = [], set()
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
                titles, anchors = _markdown_headings(target.read_text(encoding='utf-8-sig'))
                if anchor not in (titles if wiki else anchors):
                    reason = 'missing_anchor'
            if reason:
                issues.append({'file': str(path), 'line': body[:position].count('\n') + 1,
                               'target': raw, 'issue': reason})
    return {'files': len(paths), 'issues': issues, 'external_urls': sorted(urls)}


def record(kind: str, output: Path, desktop: Path | None = None) -> int:
    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('output must be inside the repository ignored artifacts directory')
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--no-index', '--', str(output)], cwd=ROOT)
    if ignored.returncode != 0 or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('output must be ignored under artifacts/')
    # Reserve the filename before executing; existing evidence is never overwritten.
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        started = datetime.now(timezone.utc).isoformat()
        head = git('rev-parse', 'HEAD')
        paths = candidates() if kind == 'quick' else documents(desktop)
        before = hashes(paths)
        sources = [Path(__file__).resolve(), ROOT / 'scripts/preflight_git.py']
        source_before = hashes(sources)
        command = [sys.executable, str(ROOT / 'scripts/preflight_git.py'), '--quick']
        if kind == 'quick':
            result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, encoding='utf-8',
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            code, text = result.returncode, result.stdout
            report = None
        else:
            report = check_links(paths)
            code, text = int(bool(report['issues'])), ''
            command = [sys.executable, str(Path(__file__).resolve()), '--kind', 'links',
                       '--output', str(output)]
            if desktop is not None:
                command += ['--desktop', str(desktop.resolve())]
        after_paths = candidates() if kind == 'quick' else documents(desktop)
        head_after = git('rev-parse', 'HEAD')
        stable = (before == hashes(after_paths) and head == head_after
                  and source_before == hashes(sources))
        code = code or int(not stable)
        metadata = {'run_id': str(uuid.uuid4()), 'kind': kind, 'started_at_utc': started,
                    'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                    'head': head, 'head_after': head_after, 'cwd': str(ROOT),
                    'command': command, 'recorder_command': [sys.executable, *sys.argv], 'exit_code': code, 'inputs_unchanged': stable,
                    'input_sha256': before, 'checker_source_sha256': source_before}
        if report is None:
            stream.write(text + '\n[RUN] ' + json.dumps(metadata, ensure_ascii=False) + '\n')
        else:
            report.update(metadata)
            report['generated_at_utc'] = metadata['finished_at_utc']
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
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
    parser.add_argument('--kind', choices=('quick', 'links', 'desktop-snapshot'), required=True)
    parser.add_argument('--output', type=Path, required=True, help='new file, or new directory for desktop-snapshot, under ignored artifacts/')
    parser.add_argument('--desktop', type=Path, help='optional desktop directory for links mode')
    parser.add_argument('--receipt', type=Path, help='existing target receipt for desktop-snapshot')
    parser.add_argument('--manifest', type=Path, help='existing declared subset for desktop-snapshot')
    args = parser.parse_args()
    if args.desktop is not None and args.kind != 'links':
        parser.error('--desktop requires --kind links')
    if args.kind == 'desktop-snapshot' and (args.receipt is None or args.manifest is None):
        parser.error('desktop-snapshot requires --receipt and --manifest')
    if args.kind != 'desktop-snapshot' and (args.receipt is not None or args.manifest is not None):
        parser.error('--receipt/--manifest require --kind desktop-snapshot')
    try:
        if args.kind == 'desktop-snapshot':
            return record_desktop_snapshot(args.output, args.receipt, args.manifest)
        return record(args.kind, args.output, args.desktop)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'Recording failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
