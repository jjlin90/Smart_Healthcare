"""Verify local inference files against the recorded artifact fingerprints."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bert', type=Path, required=True)
    parser.add_argument('--vector', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] / 'evaluation/model_artifacts.json')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
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
    print('Artifact fingerprints match.' if not errors else 'Do not attribute historical metrics to these different artifacts.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
