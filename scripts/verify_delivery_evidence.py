"""Verify supplied local delivery evidence and summarize its recorded results."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.record_document_checks import latest_test_validation, check_links
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


def rebase_markdown(source: Path, target: Path, content: str,
                    mapping: dict[Path, Path], root: Path) -> str:
    """Shared by desktop writing and verification; fenced/inline code stays literal."""
    def map_link(match: re.Match) -> str:
        label, raw = match.groups()
        raw = raw.strip('<>')
        if raw.startswith('#') or (not re.match(r'^[A-Za-z]:[/\\]', raw) and
                re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', raw)) or raw.startswith('//'):
            return match[0]
        if re.match(r'^[A-Za-z]:[/\\]', raw):
            return match[0]
        dest, marker, anchor = unquote(raw).partition('#')
        original = (source.parent / dest).resolve()
        mapped = mapping.get(original)
        if mapped is not None:
            destination = Path(os.path.relpath(mapped, target.parent)).as_posix()
        elif original.is_file() and original.is_relative_to(root):
            destination = original.as_posix()
        else:
            return match[0]
        destination += marker + anchor
        if ' ' in destination:
            destination = '<' + destination + '>'
        return f'[{label}]({destination})'
    blocks = re.split(r'(^```[^\n]*\n.*?^```[ \t]*\r?$)', content, flags=re.M | re.S)
    def prose(block):
        pieces = re.split(r'(`+[^\n]*?`+)', block)
        return ''.join(piece if piece.startswith('`') else re.sub(
            r'\[([^\]\n]+)\]\((<[^>\n]+>|[^)\s]+)\)', map_link, piece) for piece in pieces)
    return ''.join(block if block.startswith('```') else prose(block) for block in blocks)


def project_desktop_document(source: Path, target: Path | None = None,
                             mapping: dict[Path, Path] | None = None) -> bytes:
    target = target or Path.home() / 'Desktop' / source.name
    if mapping is None:
        mapping = {(ROOT / 'docs' / name).resolve(): target.parent / name for name in PRIMARY_DOCUMENTS}
    return rebase_markdown(source, target, source.read_bytes().decode('utf-8-sig'), mapping, ROOT).encode('utf-8')


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
    mapping = {}
    for target in receipt:
        for directory in (ROOT / 'docs', ROOT / 'docs/MedAgent_逐题适配'):
            source = directory / target.name
            if source.is_file():
                mapping[source.resolve()] = target
    for name in PRIMARY_DOCUMENTS:
        targets = [target for target in seen if target.name == name]
        require(len(targets) == 1, 'manifest: missing primary document or ambiguous mapping')
        repository_source = ROOT / 'docs' / name
        require(targets[0].read_bytes() == project_desktop_document(repository_source, targets[0], mapping), 'desktop: content differs from current repository after link mapping')
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


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_current_delivery(pointer_path: Path) -> dict:
    """Recheck current MedAgent outputs without copying or rewriting any target."""
    pointer = json.loads(pointer_path.read_text(encoding='utf-8-sig'))
    report_path = Path(pointer['report'])
    require(sha(report_path) == pointer['report_sha256'], 'delivery: report hash differs')
    report = json.loads(report_path.read_text(encoding='utf-8-sig'))
    require(report['validation_scope'] == 'current_documents_and_original_references', 'delivery: invalid scope')
    documents = {Path(p): digest for p, digest in report['current_document_sha256'].items()}
    require(bool(documents), 'delivery: empty document scope')
    for path, digest in documents.items():
        require(sha(path) == digest, 'delivery: current document hash differs')
    links = check_links(list(documents))
    require(links['issues'] == [], 'delivery: current local links failed')
    require(links['files'] == report['document_links_checked'], 'delivery: link count differs')
    library = ROOT / 'docs/MedAgent_逐题适配'
    coverage_path = library / 'coverage.json'
    require(sha(coverage_path) == report['coverage_sha256'], 'delivery: coverage hash differs')
    coverage = json.loads(coverage_path.read_text(encoding='utf-8-sig'))
    book_path = ROOT / 'docs/MedAgent_AI_多Agent项目话术.md'
    require(sha(book_path) == report['book_sha256'], 'delivery: book hash differs')
    book = book_path.read_text(encoding='utf-8-sig')
    require(len(coverage['questions']) == report['question_count'] == coverage['question_count'], 'delivery: question count differs')
    require(len(coverage['examples']) == report['example_count'] == coverage['example_count'], 'delivery: example count differs')
    originals = {}
    for source in coverage['source_files']:
        path = ROOT / source['original_local_path']
        require(sha(path) == source['sha256'], 'delivery: original source hash differs')
        originals[source['original_local_path']] = path.read_text(encoding='utf-8-sig')
    for row in coverage['questions']:
        original_line = originals[row['original_local_path']].splitlines()[row['line']-1]
        require(row['question'] in original_line, 'delivery: question source differs')
        require(book.count('### ' + row['book_anchor'] + '\n') == 1, 'delivery: question anchor missing or duplicated')
        require(row.get('display_question', row['question']) in book, 'delivery: displayed question missing')
        if 'source_start_column' in row:
            require(original_line[row['source_start_column']-1:row['source_end_column']] == row['question'], 'delivery: question character span differs')
    adaptations = {'unchanged': 0, 'tob_rewrite': 0}
    for row in coverage['examples']:
        original = originals[row['original_local_path']]
        blocks = list(re.finditer(r'^```[^\n]*\n(.*?)^```\s*$', original, re.M | re.S))
        block = next((b for b in blocks if original[:b.start()].count('\n')+1 == row['line']), None)
        require(block is not None and hashlib.sha256(block[0].encode()).hexdigest() == row['source_sha256'], 'delivery: original example differs')
        content = block[1].rstrip()
        require(hashlib.sha256(content.encode()).hexdigest() == row['source_content_sha256'], 'delivery: source content hash differs')
        heading = '#### ' + row['book_anchor'] + '\n'
        require(book.count(heading) == 1, 'delivery: example anchor missing or duplicated')
        body = book.split(heading, 1)[1].split('```'+row['language']+'\n', 1)[1].split('\n```', 1)[0]
        require(hashlib.sha256(body.encode()).hexdigest() == row['adapted_sha256'], 'delivery: adapted example differs')
        kind = 'unchanged' if body == content else 'tob_rewrite'
        require(row['adaptation'] == kind, 'delivery: example adaptation differs')
        adaptations[kind] += 1
    require(adaptations == coverage['example_adaptation_counts'] == report['example_adaptations_verified'], 'delivery: adaptation counts differ')
    receipt_path = pointer_path.parent / 'desktop_sync_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    entries = receipt['entries']
    require(len(entries) == report['desktop_hashes_verified'] and len({r['target'] for r in entries}) == len(entries), 'delivery: inconsistent receipt targets')
    require(all(Path(row['target']).name != 'med_rag_面试全解.md' for row in entries), 'delivery: independent RAG target included')
    mapping = {Path(row['source']).resolve(): Path(row['target']) for row in entries}
    for row in entries:
        source, target = Path(row['source']), Path(row['target'])
        require(sha(source) == row['source_sha256'] and sha(target) == row['target_sha256'], 'delivery: sync hash differs')
        expected = project_desktop_document(source, target, mapping) if source.suffix.lower() == '.md' else source.read_bytes()
        require(target.read_bytes() == expected, 'delivery: desktop content differs after shared mapping')
    validation = latest_test_validation()
    require(validation is not None, 'delivery: no current successful code regression')
    passed = validation['test_counts']['passed']
    claims = re.findall(r'(?:本轮|最新)代码与依赖回归\s+(\d+)\s+项通过', book)
    for path in documents:
        if path.name in ('README.md', 'MedAgent_项目理解指南.md', 'MedAgent_文档对照与工程改进_2026-10-13.md') and path.is_relative_to(ROOT):
            claims += re.findall(r'(?:本轮|最新)代码与依赖回归\s+(\d+)\s+项通过', path.read_text(encoding='utf-8-sig'))
    require(bool(claims) and all(int(n) == passed for n in claims), 'delivery: current document test claim differs')
    return {'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'head': verification_head(),
            'pytest_passed': passed, 'document_files_checked': len(documents), 'document_issues': 0,
            'questions_verified': len(coverage['questions']), 'examples_verified': len(coverage['examples']),
            'example_adaptations': adaptations, 'desktop_targets_matched': len(entries),
            'verifier_path': 'scripts/verify_delivery_evidence.py', 'verifier_sha256': sha(Path(__file__)),
            'delivery_report_sha256': sha(report_path), 'test_run_id': validation['run_id'],
            'scope': 'Current code regression, document claims, source references, examples, links and desktop content; no writes or backups.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--evidence-dir', type=Path, help='historical full/quick evidence format; target drift is an error')
    mode.add_argument('--delivery-pointer', type=Path, help='current MedAgent document delivery pointer')
    parser.add_argument('--output', type=Path, help='Create a new JSON summary; existing files are never overwritten')
    args = parser.parse_args()
    try:
        result = (verify_current_delivery(args.delivery_pointer.resolve()) if args.delivery_pointer
                  else summarize(args.evidence_dir.resolve()))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            with args.output.open('x', encoding='utf-8', newline='\n') as output:
                output.write(rendered + '\n')
        print(rendered)
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as exc:
        print(f'Evidence verification failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
