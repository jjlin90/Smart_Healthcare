"""Verify local inference files against the recorded artifact fingerprints."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_validation_provenance(record: dict, bert: Path, root: Path = ROOT) -> None:
    relative = Path(record['file'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe validation dataset path')
    raw = (root / relative).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['repository_sha256']:
        raise ValueError('Repository validation bytes differ')
    normalized = raw.replace(b'\r\n', b'\n')
    ending = record.get('original_line_endings', 'CRLF')
    if ending not in ('CRLF', 'LF'):
        raise ValueError('Unsupported original validation line endings')
    original = normalized.replace(b'\n', b'\r\n') if ending == 'CRLF' else normalized
    if hashlib.sha256(original).hexdigest() != record['original_sha256']:
        raise ValueError('Original CRLF validation fingerprint differs')
    items = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    rows = [(item['text'], tuple(item['expected_intents'])) for item in items]
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode('utf-8')).hexdigest()
    thresholds = json.loads((bert / 'intent_thresholds.json').read_text(encoding='utf-8'))
    if digest != record['rows_sha256'] or digest != thresholds.get('validation_rows_sha256'):
        raise ValueError('Validation inputs differ from threshold artifact')
    if thresholds.get('validation_sha256') != record['original_sha256']:
        raise ValueError('Threshold artifact original validation fingerprint differs')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def record_manifest(template: dict, bert: Path, vector: Path, validation: Path, root: Path = ROOT) -> dict:
    """Build a new manifest; never change an existing historical record."""
    config = json.loads((bert / 'config.json').read_text(encoding='utf-8'))
    if config.get('problem_type') != 'multi_label_classification':
        raise ValueError('Recording requires a multi-label BERT artifact')
    thresholds = json.loads((bert / 'intent_thresholds.json').read_text(encoding='utf-8'))
    relative = validation.resolve().relative_to(root.resolve())
    raw = validation.read_bytes()
    items = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if not items:
        raise ValueError('Validation dataset is empty')
    rows = [(item['text'], tuple(item['expected_intents'])) for item in items]
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode('utf-8')).hexdigest()
    if digest != thresholds.get('validation_rows_sha256'):
        raise ValueError('Validation dataset does not match the trained threshold artifact')
    normalized = raw.replace(b'\r\n', b'\n')
    original_hash = thresholds['validation_sha256']
    if original_hash == hashlib.sha256(normalized).hexdigest():
        ending = 'LF'
    elif original_hash == hashlib.sha256(normalized.replace(b'\n', b'\r\n')).hexdigest():
        ending = 'CRLF'
    else:
        raise ValueError('Validation original bytes cannot be reproduced from this dataset')
    value = json.loads(json.dumps(template))
    value['recorded_at'] = datetime.now(timezone.utc).isoformat()
    value['note'] = 'New artifact fingerprints only. Run new evaluations for these artifacts; historical metric reports and manifests are not updated by this command.'
    for role, directory in [('bert', bert), ('vector', vector)]:
        for item in value['models'][role]['files']:
            relative_file = Path(item['file'])
            if relative_file.is_absolute() or '..' in relative_file.parts:
                raise ValueError('Manifest contains an unsafe file path')
            path = directory / relative_file
            item.update(bytes=path.stat().st_size, sha256=sha256(path))
        value['models'][role]['source'] = 'Local artifact recorded for a new evaluation; upstream revision is not inferred.'
    value['models']['bert']['validation_data'] = {
        'file': relative.as_posix(), 'original_sha256': original_hash,
        'original_line_endings': ending, 'repository_sha256': hashlib.sha256(raw).hexdigest(),
        'rows_sha256': digest,
        'note': 'Original validation bytes are distinguished from the current checkout bytes and ordered training inputs. rows_sha256 uses UTF-8 json.dumps([(text, tuple(expected_intents)), ...], ensure_ascii=False) with default separators.'
    }
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bert', type=Path, required=True)
    parser.add_argument('--vector', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] / 'evaluation/model_artifacts.json')
    parser.add_argument('--record-output', type=Path, help='Create a NEW manifest after retraining; existing output files are never overwritten')
    parser.add_argument('--validation-dataset', type=Path, help='Exact independent validation dataset used to train this BERT; required when recording')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if args.record_output:
        if not args.validation_dataset:
            parser.error('--record-output requires --validation-dataset')
        # Check the output path before expensive hashing; exclusive creation also
        # prevents races from overwriting any historical manifest.
        if args.record_output.exists():
            parser.error('Record output already exists; choose a new filename')
        value = record_manifest(manifest, args.bert, args.vector, args.validation_dataset)
        args.record_output.parent.mkdir(parents=True, exist_ok=True)
        with args.record_output.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        print('New artifact manifest recorded; run evaluation before attributing metrics to it.')
        return 0
    errors = []
    for role, directory in [('bert', args.bert), ('vector', args.vector)]:
        for item in manifest['models'][role]['files']:
            relative = Path(item['file'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Manifest contains an unsafe file path')
            file = directory / relative
            label = role + '/' + relative.as_posix()
            if not file.is_file():
                errors.append(label + ': missing')
            elif file.stat().st_size != item['bytes'] or sha256(file) != item['sha256']:
                errors.append(label + ': differs from recorded artifact')
            else:
                print('PASS ' + label)
    for error in errors:
        print('FAIL ' + error)
    provenance = manifest['models']['bert'].get('validation_data')
    if provenance:
        try:
            verify_validation_provenance(provenance, args.bert)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append('bert/validation_data: ' + str(exc))
            print('FAIL ' + errors[-1])
        else:
            print('PASS bert/validation_data (checkout bytes, original line endings, ordered training rows)')
    print('Artifact fingerprints match.' if not errors else 'Do not attribute historical metrics to these different artifacts.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
