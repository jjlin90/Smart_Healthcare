import hashlib
import json
import subprocess

import pytest

from scripts import preflight_git as preflight


@pytest.fixture(scope="module")
def mapped_repository(tmp_path_factory):
    root = tmp_path_factory.mktemp("rewrite-map")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    chunks = []
    for number in range(1, 27):
        chunks.append(f"commit refs/heads/main\nmark :{number}\n"
                      f"committer Fixture <fixture@example.invalid> {1700000000 + number} +0000\n"
                      "data 7\nfixture\n"
                      + (f"from :{number - 1}\n" if number > 1 else "")
                      + "\n")
    subprocess.run(["git", "fast-import", "--quiet"], cwd=root,
                   input="".join(chunks).encode(), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=root, check=True)
    history = subprocess.check_output(["git", "rev-list", "--reverse", "HEAD"], cwd=root).decode().splitlines()
    data = {"schema_version": 1, "commits": {
        hashlib.sha1(f"original-{i}".encode()).hexdigest(): new for i, new in enumerate(history)
    }}
    path = root / "docs/git-history-rewrite-map.json"
    path.parent.mkdir()
    return root, path, data, history


@pytest.mark.parametrize("change", ["valid", "missing", "invalid_json", "duplicate_key", "changed_pair", "missing_ancestor"])
def test_mapping_gate_rejects_corrupt_or_unavailable_history(monkeypatch, mapped_repository, change):
    root, path, data, history = mapped_repository
    baseline = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setattr(preflight, "ROOT", root)
    monkeypatch.setattr(preflight, "HISTORY_REWRITE_MAP_SHA256", hashlib.sha256(baseline).hexdigest())
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    subprocess.run(["git", "update-ref", "refs/heads/main", history[-1]], cwd=root, check=True)
    if change == "missing":
        path.unlink()
    elif change == "invalid_json":
        path.write_text("{", encoding="utf-8")
    elif change == "duplicate_key":
        path.write_text(path.read_text().replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,'), encoding="utf-8")
    elif change == "changed_pair":
        changed = json.loads(baseline)
        changed["commits"]["f" * 40] = changed["commits"].pop(next(iter(changed["commits"])))
        path.write_text(json.dumps(changed), encoding="utf-8")
    elif change == "missing_ancestor":
        subprocess.run(["git", "update-ref", "refs/heads/main", history[0]], cwd=root, check=True)
    report = preflight.Report()
    try:
        assert preflight.check_history_rewrite_map(report) is (change == "valid")
        assert bool(report.errors) is (change != "valid")
        if change == "missing_ancestor":
            assert "新 SHA 未全部位于当前 HEAD 历史" in report.errors[0]
        elif change == "changed_pair":
            assert "指纹不同" in report.errors[0]
        elif change == "duplicate_key":
            assert "重复 JSON 键" in report.errors[0]
    finally:
        subprocess.run(["git", "update-ref", "refs/heads/main", history[-1]], cwd=root, check=True)


def test_mapping_and_regressions_must_be_tracked(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    paths = [tmp_path / name for name in preflight.REQUIRED_TRACKED_PATHS
             if name not in {"docs/git-history-rewrite-map.json", "tests/test_history_rewrite_map.py"}]
    report = preflight.Report()
    preflight.check_required_project_files_tracked(report, paths)
    assert len(report.errors) == 1 and not report.passed
    assert "docs/git-history-rewrite-map.json" in report.errors[0]
    assert "tests/test_history_rewrite_map.py" in report.errors[0]
