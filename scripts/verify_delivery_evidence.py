"""Verify supplied local delivery evidence and summarize its recorded results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FILES = (
    'preflight.log', 'quick.log', 'anchor_drift.json',
    'document_links.json', 'latest_desktop_receipt.json', 'desktop_manifest.json',
)
PRIMARY_DOCUMENTS = ('MedAgent_项目理解指南.md', 'MedAgent_AI_多Agent项目话术.md')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_log(path: Path) -> str:
    data = path.read_bytes()
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    return data.decode(encoding)


def verification_head() -> str:
    return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True).strip()


def parse_preflight_logs(full: str, quick: str) -> dict[str, int]:
    """Read successful counts, rejecting missing or contradictory evidence."""
    for label, log in (('full', full), ('quick', quick)):
        require(not re.search(r'^\[(?:FAIL|WARN)\]', log, re.M), f'{label}: failed or warning checks')
        require('结论：检查通过，可以进入提交/推送流程。' in log, f'{label}: missing successful conclusion')
    full_passes = re.findall(r'^\[PASS\] (.+)$', full, re.M)
    quick_passes = re.findall(r'^\[PASS\] (.+)$', quick, re.M)
    full_passes = [line.rstrip('\r') for line in full_passes]
    quick_passes = [line.rstrip('\r') for line in quick_passes]
    require(len(set(full_passes)) == len(full_passes), 'full: duplicate successful checks')
    require(len(set(quick_passes)) == len(quick_passes), 'quick: duplicate successful checks')
    pytest_lines = [line for line in full_passes if line.startswith('pytest 通过：')]
    require(len(pytest_lines) == 1, 'full: expected one pytest result')
    match = re.fullmatch(r'pytest 通过：(\d+) passed(?:, \d+ warnings?)? in [\d.]+s', pytest_lines[0])
    require(match is not None, 'full: unrecognized pytest summary')
    pytest_passed = int(match.group(1))
    require(pytest_passed > 0, 'full: no passed tests')
    pip_line = 'pip check 通过：No broken requirements found.'
    require(full_passes.count(pip_line) == 1, 'full: missing successful pip check')
    static_passes = [line for line in full_passes if line not in (pytest_lines[0], pip_line)]
    require(bool(quick_passes) and static_passes == quick_passes, 'full/quick: static results differ')
    return {
        'pytest_passed': pytest_passed,
        'full_preflight_passed': len(full_passes),
        'quick_preflight_passed': len(quick_passes),
    }


def project_desktop_document(source: Path) -> bytes:
    """Apply the documented desktop link mapping to repository text."""
    def map_link(match: re.Match) -> str:
        dest = match[2].strip().strip('<>')
        if dest.startswith(('http:', 'https:', 'mailto:', '#')):
            return match[0]
        file, sep, anchor = dest.partition('#')
        if file in PRIMARY_DOCUMENTS:
            return match[0]
        mapped = (source.parent / file).resolve().as_posix()
        return match[1] + '<' + mapped + (sep + anchor if sep else '') + '>)'
    text = re.sub(r'(\[[^\]\n]+\]\()([^\)\n]+)\)', map_link, source.read_text(encoding='utf-8'))
    return text.encode('utf-8')


def verify_desktop_documents(evidence_dir: Path, receipt_entries: list[dict]) -> dict[str, str]:
    manifest = json.loads((evidence_dir / 'desktop_manifest.json').read_text(encoding='utf-8-sig'))
    require(isinstance(manifest, list) and bool(manifest), 'manifest: missing synchronization entries')
    receipt = {Path(item['target']).resolve(): item for item in receipt_entries}
    seen = set()
    for item in manifest:
        target = Path(item['target']).resolve()
        require(target not in seen, 'manifest: duplicate target')
        seen.add(target)
        require(target in receipt, 'manifest: target missing from current receipt')
        source = Path(item['source']).resolve()
        require(source.is_relative_to(evidence_dir.resolve()), 'manifest: projected source outside evidence directory')
        projected = source.read_bytes()
        current = target.read_bytes()
        require(projected == current, 'manifest: source/target bytes differ')
        digest = hashlib.sha256(projected).hexdigest()
        require(digest == item['after_sha256'] == receipt[target]['sha256'], 'manifest: source/receipt hash differs')
    repository_hashes = {}
    for name in PRIMARY_DOCUMENTS:
        targets = [target for target in seen if target.name == name]
        require(len(targets) == 1, 'manifest: missing primary document or ambiguous mapping')
        repository_source = ROOT / 'docs' / name
        require(targets[0].read_bytes() == project_desktop_document(repository_source), 'desktop: content differs from current repository after link mapping')
        repository_hashes['docs/' + name] = hashlib.sha256(repository_source.read_bytes()).hexdigest()
    return repository_hashes


def summarize(evidence_dir: Path) -> dict:
    counts = parse_preflight_logs(read_log(evidence_dir / 'preflight.log'), read_log(evidence_dir / 'quick.log'))
    read_json = lambda name: json.loads((evidence_dir / name).read_text(encoding='utf-8-sig'))
    links = read_json('document_links.json')
    require(isinstance(links['files'], int) and links['files'] > 0, 'links: invalid file count')
    require(links['issues'] == [], 'links: unresolved issues')
    probe = read_json('anchor_drift.json')
    expected = {(surface, change) for surface in ('docker', 'ci') for change in ('original', 'missing', 'duplicate')}
    require(len(probe) == len(expected) and {(item['surface'], item['change']) for item in probe} == expected, 'anchor: missing or duplicate scenarios')
    for item in probe:
        require(item['valid_install_fixture'] is True, 'anchor: invalid installation fixture')
        expected_result = 'passed' if item['change'] == 'original' else 'blocked_before_gate'
        require(item['result'] == expected_result, 'anchor: unexpected result')
    receipt = read_json('latest_desktop_receipt.json')
    entries = receipt['entries']
    require(receipt['count'] == len(entries) and bool(entries), 'receipt: inconsistent target count')
    require(len({item['target'] for item in entries}) == len(entries), 'receipt: duplicate targets')
    for item in entries:
        require(hashlib.sha256(Path(item['target']).read_bytes()).hexdigest() == item['sha256'], 'receipt: current target hash differs')
    repository_hashes = verify_desktop_documents(evidence_dir, entries)
    return {
        'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'head': verification_head(),
        **counts,
        'document_files_checked': links['files'],
        'document_issues': len(links['issues']),
        'desktop_targets_matched': len(entries),
        'desktop_manifest_targets_matched': len(read_json('desktop_manifest.json')),
        'anchor_scenarios_checked': len(probe),
        'repository_desktop_documents_matched': len(repository_hashes),
        'repository_document_sha256': repository_hashes,
        'files': {name: hashlib.sha256((evidence_dir / name).read_bytes()).hexdigest() for name in EVIDENCE_FILES},
        'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'Counts are parsed from supplied logs/reports. All receipt targets have their current hashes checked. The manifest covers only its declared subset of synchronized source/target bytes; two primary desktop documents are also compared against current repository text. Verification time and head describe this verification, not the test execution commit. Local reports and desktop targets are not distributed with Git.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--output', type=Path, help='Create a new JSON summary; existing files are never overwritten')
    args = parser.parse_args()
    try:
        result = summarize(args.evidence_dir.resolve())
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            with args.output.open('x', encoding='utf-8', newline='\n') as output:
                output.write(rendered + '\n')
        print(rendered)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'Evidence verification failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
