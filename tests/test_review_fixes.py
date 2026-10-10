import json

import pytest
from pydantic import ValidationError

from backend.a2a_context import HISTORY_BYTES, bounded_history, history_size
from backend.a2a_server import SpecialistRequest


def test_missing_yaml_reports_development_install_instruction(monkeypatch):
    import builtins
    from scripts import preflight_git
    original_import = builtins.__import__
    def missing_yaml(name, *args, **kwargs):
        if name == 'yaml':
            raise ImportError('No module named yaml')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing_yaml)
    report = preflight_git.Report()
    preflight_git.check_kubernetes_intent_config(report)
    assert len(report.errors) == 1
    assert '缺少开发依赖 PyYAML' in report.errors[0]
    assert 'python -m pip install -r requirements-dev.txt -c constraints.txt' in report.errors[0]
    assert '同一 Python' in report.errors[0]
    assert not report.passed


@pytest.mark.parametrize("change", [None, "old_model", "old_threshold", "missing_mount", "override", "invalid_yaml"])
def test_deployment_gate_checks_configmap_not_local_environment(monkeypatch, tmp_path, change):
    from pathlib import Path
    from scripts import preflight_git
    text = (Path(__file__).resolve().parents[1] / "deploy/k8s/medagent-api.yaml").read_text(encoding="utf-8")
    if change == "old_model":
        text = text.replace("medical_intent_bert_multilabel_v4", "medical_intent_bert_tob_v3")
    elif change == "old_threshold":
        text = text.replace("INTENT_BERT_THRESHOLD: '0.5'", "INTENT_BERT_THRESHOLD: '0.95'")
    elif change == "missing_mount":
        text = text.replace("mountPath: /models", "mountPath: /wrong")
    elif change == "override":
        text = text.replace("        envFrom:", "        env:\n        - name: INTENT_BERT_MODEL_PATH\n          value: /models/old\n        envFrom:", 1)
    elif change == "invalid_yaml":
        text = "data: [invalid"
    target = tmp_path / "deploy/k8s/medagent-api.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(preflight_git, "ROOT", tmp_path)
    monkeypatch.setenv("INTENT_BERT_MODEL_PATH", "/models/medical_intent_bert_multilabel_v4")
    report = preflight_git.Report()
    preflight_git.check_kubernetes_intent_config(report)
    assert bool(report.errors) is (change is not None)


@pytest.mark.parametrize("ending", ["lf", "crlf", "internal"])
def test_validation_fingerprints_preserve_bytes_and_order(tmp_path, ending):
    import hashlib
    from scripts.train_intent_bert import validation_fingerprints
    rows = [("临床文本", ("症状评估", "药品信息查询"))]
    raw = (json.dumps({"text": rows[0][0], "expected_intents": rows[0][1]}, ensure_ascii=False) + "\n").encode()
    path = None
    if ending != "internal":
        path = tmp_path / "validation.jsonl"
        path.write_bytes(raw.replace(b"\n", b"\r\n") if ending == "crlf" else raw)
    result = validation_fingerprints(rows, path)
    serialized = json.dumps(rows, ensure_ascii=False).encode()
    assert result["validation_rows_sha256"] == hashlib.sha256(serialized).hexdigest()
    assert result["validation_sha256"] == hashlib.sha256(path.read_bytes() if path else serialized).hexdigest()
    assert result["repository_validation_sha256"] == hashlib.sha256(raw if path else serialized).hexdigest()
    assert validation_fingerprints([(rows[0][0], tuple(reversed(rows[0][1])))], path)["validation_rows_sha256"] != result["validation_rows_sha256"]


def test_recorded_validation_provenance_detects_content_changes(tmp_path):
    import hashlib
    from scripts.verify_model_artifacts import verify_validation_provenance
    raw = (json.dumps({"text": "验证文本", "expected_intents": ["症状评估"]}, ensure_ascii=False) + "\n").encode()
    digest = hashlib.sha256(json.dumps([("验证文本", ("症状评估",))], ensure_ascii=False).encode()).hexdigest()
    record = {"file": "validation.jsonl", "repository_sha256": hashlib.sha256(raw).hexdigest(),
              "original_sha256": hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest(), "rows_sha256": digest}
    (tmp_path / "validation.jsonl").write_bytes(raw)
    (tmp_path / "intent_thresholds.json").write_text(json.dumps({"validation_sha256": record["original_sha256"], "validation_rows_sha256": digest}), encoding="utf-8")
    verify_validation_provenance(record, tmp_path, tmp_path)
    (tmp_path / "validation.jsonl").write_bytes(raw.replace("验证文本".encode(), "其他文本".encode()))
    with pytest.raises(ValueError, match="Repository validation"):
        verify_validation_provenance(record, tmp_path, tmp_path)


@pytest.mark.parametrize('ending', ['LF', 'CRLF'])
def test_new_manifest_can_be_recorded_and_verified_after_retraining(tmp_path, ending):
    import hashlib
    from scripts.verify_model_artifacts import record_manifest, verify_validation_provenance, sha256
    from scripts.train_intent_bert import validation_fingerprints
    bert, vector = tmp_path / 'bert', tmp_path / 'vector'
    bert.mkdir()
    vector.mkdir()
    rows = [('新验证样本', ('症状评估',))]
    raw = (json.dumps({'text': rows[0][0], 'expected_intents': rows[0][1]}, ensure_ascii=False) + '\n').encode()
    validation = tmp_path / 'validation.jsonl'
    validation.write_bytes(raw.replace(b'\n', b'\r\n') if ending == 'CRLF' else raw)
    fingerprints = validation_fingerprints(rows, validation)
    (bert / 'config.json').write_text(json.dumps({'problem_type': 'multi_label_classification'}), encoding='utf-8')
    (bert / 'intent_thresholds.json').write_text(json.dumps({'thresholds': {'症状评估': 0.5}, **fingerprints}), encoding='utf-8')
    (vector / 'config.json').write_text('{}', encoding='utf-8')
    template = {'models': {'bert': {'files': [{'file': 'config.json'}, {'file': 'intent_thresholds.json'}]}, 'vector': {'files': [{'file': 'config.json'}]}}}
    before = json.dumps(template)
    record = record_manifest(template, bert, vector, validation, tmp_path)
    assert json.dumps(template) == before
    assert record['models']['bert']['files'][1]['sha256'] == sha256(bert / 'intent_thresholds.json')
    assert record['models']['bert']['validation_data']['original_line_endings'] == ending
    verify_validation_provenance(record['models']['bert']['validation_data'], bert, tmp_path)
    (bert / 'intent_thresholds.json').write_text(json.dumps({'validation_sha256': hashlib.sha256(raw).hexdigest(), 'validation_rows_sha256': 'wrong'}), encoding='utf-8')
    with pytest.raises(ValueError, match='does not match'):
        record_manifest(template, bert, vector, validation, tmp_path)


def test_manifest_recording_never_overwrites_existing_output(monkeypatch, tmp_path):
    import sys
    from scripts.verify_model_artifacts import main
    output = tmp_path / 'history.json'
    output.write_text('{"historical": true}', encoding='utf-8')
    before = output.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['verify', '--bert', 'unused', '--vector', 'unused', '--record-output', str(output), '--validation-dataset', 'unused'])
    with pytest.raises(SystemExit):
        main()
    assert output.read_bytes() == before


def test_preflight_detects_missing_development_dependency_pin(monkeypatch, tmp_path):
    from scripts import preflight_git
    monkeypatch.setattr(preflight_git, 'ROOT', tmp_path)
    (tmp_path / 'pyproject.toml').write_text('[project]\ndependencies=["fastapi>=0.141"]\n[project.optional-dependencies]\ndev=["PyYAML>=6.0,<7"]', encoding='utf-8')
    (tmp_path / 'requirements.txt').write_text('fastapi>=0.141', encoding='utf-8')
    (tmp_path / 'requirements-dev.txt').write_text('-r requirements.txt\nPyYAML>=6.0,<7', encoding='utf-8')
    (tmp_path / 'constraints.txt').write_text('fastapi==0.141.1', encoding='utf-8')
    report = preflight_git.Report()
    preflight_git.check_direct_dependency_constraints(report)
    assert report.errors and 'pyyaml' in report.errors[0]
    (tmp_path / 'constraints.txt').write_text('fastapi==0.141.1\nPyYAML==6.0.3', encoding='utf-8')
    report = preflight_git.Report()
    preflight_git.check_direct_dependency_constraints(report)
    assert not report.errors


@pytest.mark.parametrize('change, expected', [
    (None, None),
    ('runtime', '开发工具不能进入运行依赖'),
    ('core', '开发工具不能进入运行依赖'),
    ('both', '开发工具不能进入运行依赖'),
    ('dev_version', '开发依赖声明与 pyproject 不一致'),
    ('include', '通过 -r requirements.txt 包含运行依赖'),
    ('duplicate', '重复声明依赖'),
    ('missing_tool', '开发依赖缺少仓库工具'),
    ('relative_include', None),
    ('wrong_include', '通过 -r requirements.txt 包含运行依赖'),
    ('glued_manifest_include', '通过 -r requirements.txt 包含运行依赖'),
    ('long_manifest_include', '通过 -r requirements.txt 包含运行依赖'),
])
def test_dependency_classification_rejects_regressions(monkeypatch, tmp_path, change, expected):
    from scripts import preflight_git
    runtime = 'fastapi>=0.141\n'
    dev = '-r requirements.txt\nPyYAML>=6.0,<7\npytest>=9.1\npytest-asyncio>=1.4\nlocust>=2.46\n'
    core = ['fastapi>=0.141']
    extra = ['PyYAML>=6.0,<7', 'pytest>=9.1', 'pytest-asyncio>=1.4', 'locust>=2.46']
    if change == 'runtime':
        runtime += 'pytest>=9.1\n'
    elif change == 'core':
        core.append('locust>=2.46')
    elif change == 'both':
        runtime += 'PyYAML>=6.0,<7\n'
        core.append('PyYAML>=6.0,<7')
    elif change == 'dev_version':
        dev = dev.replace('pytest>=9.1', 'pytest>=8.0')
    elif change == 'include':
        dev = dev.replace('-r requirements.txt\n', '')
    elif change == 'duplicate':
        dev += 'pytest>=9.1\n'
    elif change == 'missing_tool':
        dev = dev.replace('locust>=2.46\n', '')
        extra.remove('locust>=2.46')
    elif change == 'relative_include':
        dev = dev.replace('-r requirements.txt', '-r ./requirements.txt')
    elif change == 'wrong_include':
        dev = dev.replace('-r requirements.txt', '-r other.txt')
    elif change == 'glued_manifest_include':
        dev = dev.replace('-r requirements.txt', '-r./requirements.txt')
    elif change == 'long_manifest_include':
        dev = dev.replace('-r requirements.txt', '--requirement requirements.txt')
    (tmp_path / 'requirements.txt').write_text(runtime, encoding='utf-8')
    (tmp_path / 'requirements-dev.txt').write_text(dev, encoding='utf-8')
    (tmp_path / 'pyproject.toml').write_text('[project]\ndependencies = ' + json.dumps(core) + '\n[project.optional-dependencies]\ndev = ' + json.dumps(extra), encoding='utf-8')
    monkeypatch.setattr(preflight_git, 'ROOT', tmp_path)
    report = preflight_git.Report()
    preflight_git.check_dependency_classification(report)
    if expected:
        assert len(report.errors) == 1 and expected in report.errors[0]
        assert not report.passed
    else:
        assert not report.errors and len(report.passed) == 1


@pytest.mark.parametrize('quick', [False, True])
def test_dependency_classification_blocks_preflight_entry(monkeypatch, quick):
    import sys
    from scripts import preflight_git
    monkeypatch.setattr(sys, 'argv', ['preflight_git.py'] + (['--quick'] if quick else []))
    monkeypatch.setattr(preflight_git, 'is_git_repository', lambda: True)
    monkeypatch.setattr(preflight_git, 'git_paths', lambda *args: [])
    for name in vars(preflight_git):
        if name.startswith('check_') and name != 'check_dependency_classification':
            monkeypatch.setattr(preflight_git, name, lambda *args: None)
    monkeypatch.setattr(preflight_git, 'run_quality_commands', lambda *args: None)
    monkeypatch.setattr(preflight_git, 'check_dependency_classification', lambda report: report.error('开发工具不能进入运行依赖'))
    assert preflight_git.main() == 1


@pytest.mark.parametrize('change, expected', [
    (None, None),
    ('docker_dev', '生产 Docker 必须仅通过'),
    ('docker_extra', '生产 Docker 必须仅通过'),
    ('docker_missing', '生产 Docker 缺少可检查'),
    ('docker_constraints', '生产 Docker 必须仅通过'),
    ('ci_runtime', '验证 CI 必须仅通过'),
    ('ci_extra', '验证 CI 必须仅通过'),
    ('ci_constraints', '验证 CI 必须仅通过'),
    ('ci_missing', '验证 CI 缺少可检查'),
    ('docker_module', None),
    ('ci_relative', None),
])
def test_install_entry_gate_rejects_wrong_production_and_ci_installs(monkeypatch, tmp_path, change, expected):
    from pathlib import Path
    from scripts import preflight_git
    root = Path(__file__).resolve().parents[1]
    docker = (root / 'Dockerfile').read_text(encoding='utf-8')
    workflow = (root / '.github/workflows/verify.yml').read_text(encoding='utf-8')
    if change == 'docker_dev':
        docker = docker.replace('-r requirements.txt', '-r requirements-dev.txt')
    elif change == 'docker_extra':
        docker += '\nRUN pip install pytest\n'
    elif change == 'docker_missing':
        docker = docker.replace('RUN pip install', '# RUN pip install')
    elif change == 'docker_constraints':
        docker = docker.replace(' -c constraints.txt', '')
    elif change == 'ci_runtime':
        workflow = workflow.replace('-r requirements-dev.txt', '-r requirements.txt')
    elif change == 'ci_extra':
        workflow = workflow.replace('run: python -m pip install -r requirements-dev.txt -c constraints.txt',
                                    'run: |\n          python -m pip install -r requirements-dev.txt -c constraints.txt\n          pip install locust')
    elif change == 'ci_constraints':
        workflow = workflow.replace(' -c constraints.txt', '')
    elif change == 'ci_missing':
        workflow = workflow.replace('python -m pip install -r requirements-dev.txt -c constraints.txt', 'echo skipped')
    elif change == 'docker_module':
        docker = docker.replace('pip install', 'python -m pip install')
    elif change == 'ci_relative':
        workflow = workflow.replace('-r requirements-dev.txt -c constraints.txt', '-r ./requirements-dev.txt -c ./constraints.txt')
    (tmp_path / 'Dockerfile').write_text(docker, encoding='utf-8')
    target = tmp_path / '.github/workflows/verify.yml'
    target.parent.mkdir(parents=True)
    target.write_text(workflow, encoding='utf-8')
    monkeypatch.setattr(preflight_git, 'ROOT', tmp_path)
    report = preflight_git.Report()
    preflight_git.check_dependency_install_entries(report)
    if expected:
        assert len(report.errors) == 1 and expected in report.errors[0]
        assert not report.passed
    else:
        assert not report.errors and len(report.passed) == 1


@pytest.mark.parametrize('surface, command', [
    ('docker', 'pip install -r./requirements.txt -c./constraints.txt'),
    ('ci', 'python -m pip install --requirement=./requirements-dev.txt --constraint=./constraints.txt'),
    ('docker', 'pip install --requirement requirements.txt --constraint constraints.txt'),
    ('docker', 'pip3 install -r requirements.txt -c constraints.txt'),
    ('docker', 'python3.11 -m pip install -r requirements.txt -c constraints.txt'),
    ('ci', 'python3.11 -m pip install -r requirements-dev.txt -c constraints.txt'),
    ('docker', 'pip install --upgrade --quiet -q --disable-pip-version-check --no-cache-dir -r requirements.txt -c constraints.txt'),
    ('ci', 'pip install --upgrade --quiet -q --disable-pip-version-check --no-cache-dir -r requirements-dev.txt -c constraints.txt'),
])
def test_install_entry_gate_accepts_documented_spellings(monkeypatch, tmp_path, surface, command):
    from pathlib import Path
    from scripts import preflight_git
    root = Path(__file__).resolve().parents[1]
    docker = (root / 'Dockerfile').read_text(encoding='utf-8')
    workflow = (root / '.github/workflows/verify.yml').read_text(encoding='utf-8')
    if surface == 'docker':
        anchor = 'pip install --no-cache-dir -r requirements.txt -c constraints.txt'
        assert docker.count(anchor) == 1, 'Docker 安装注入锚点必须唯一；入口变化后须同步更新测试'
        modified = docker.replace(anchor, command)
        assert modified != docker and modified.count(command) == 1, 'Docker 被测安装命令未成功注入'
        docker = modified
    else:
        anchor = 'python -m pip install -r requirements-dev.txt -c constraints.txt'
        assert workflow.count(anchor) == 1, 'CI 安装注入锚点必须唯一；入口变化后须同步更新测试'
        modified = workflow.replace(anchor, command)
        assert modified != workflow and modified.count(command) == 1, 'CI 被测安装命令未成功注入'
        workflow = modified
    (tmp_path / 'Dockerfile').write_text(docker, encoding='utf-8')
    target = tmp_path / '.github/workflows/verify.yml'
    target.parent.mkdir(parents=True)
    target.write_text(workflow, encoding='utf-8')
    monkeypatch.setattr(preflight_git, 'ROOT', tmp_path)
    report = preflight_git.Report()
    preflight_git.check_dependency_install_entries(report)
    assert report.errors == []
    assert len(report.passed) == 1
    assert '生产 Docker/验证 CI' in report.passed[0]


@pytest.mark.parametrize('quick', [False, True])
def test_install_entry_gate_blocks_preflight_entry(monkeypatch, quick):
    import sys
    from scripts import preflight_git
    monkeypatch.setattr(sys, 'argv', ['preflight_git.py'] + (['--quick'] if quick else []))
    monkeypatch.setattr(preflight_git, 'is_git_repository', lambda: True)
    monkeypatch.setattr(preflight_git, 'git_paths', lambda *args: [])
    for name in vars(preflight_git):
        if name.startswith('check_') and name != 'check_dependency_install_entries':
            monkeypatch.setattr(preflight_git, name, lambda *args: None)
    monkeypatch.setattr(preflight_git, 'run_quality_commands', lambda *args: None)
    monkeypatch.setattr(preflight_git, 'check_dependency_install_entries', lambda report: report.error('生产 Docker 安装入口错误'))
    assert preflight_git.main() == 1


def test_preflight_detects_stale_newly_staged_content(monkeypatch):
    from types import SimpleNamespace
    from scripts import preflight_git
    monkeypatch.setattr(preflight_git, 'run', lambda command: SimpleNamespace(returncode=0, stdout='docs/intent-fusion.md\0' if '--cached' in command else 'docs/intent-fusion.md\0README.md\0'))
    report = preflight_git.Report()
    preflight_git.check_new_staged_content(report)
    assert report.errors and 'docs/intent-fusion.md' in report.errors[0]
    monkeypatch.setattr(preflight_git, 'run', lambda command: SimpleNamespace(returncode=0, stdout='docs/intent-fusion.md\0' if '--cached' in command else 'README.md\0'))
    report = preflight_git.Report()
    preflight_git.check_new_staged_content(report)
    assert not report.errors


def test_vector_calibration_distinguishes_top1_hit_from_set_match(monkeypatch, tmp_path):
    import sys
    import numpy as np
    from types import SimpleNamespace
    from scripts import calibrate_intent_vector as calibration
    cases = [{"id": "multi", "text": "multi", "expected_intents": ["症状评估", "药品信息查询"]},
             {"id": "ood", "text": "ood", "expected_intents": []},
             {"id": "single", "text": "single", "expected_intents": ["症状评估"]}]
    dataset, prototypes, output = (tmp_path / name for name in ("cases.jsonl", "prototypes.jsonl", "result.json"))
    dataset.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases), encoding="utf-8")
    prototypes.write_text("{}", encoding="utf-8")
    model = SimpleNamespace(encode=lambda *args, **kwargs: np.array([[0.9, 0.1], [0.8, 0.1], [0.7, 0.1]]))
    monkeypatch.setattr(calibration, "_load_vector_bundle", lambda *args: (model, ["症状评估", "药品信息查询"], np.eye(2)))
    monkeypatch.setattr(calibration, "get_settings", lambda: SimpleNamespace(intent_vector_model_path="unused", intent_prototypes_path=str(prototypes)))
    monkeypatch.setattr(sys, "argv", ["calibrate", "--dataset", str(dataset), "--thresholds", "0.5,0.99", "--output", str(output)])
    calibration.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert "bert_threshold" not in result
    assert result["thresholds"][0]["accepted_top1_hit_rate"] == pytest.approx(2 / 3)
    assert result["thresholds"][0]["accepted_singleton_exact_match"] == pytest.approx(1 / 3)
    assert result["thresholds"][1]["accepted_top1_hit_rate"] is None
    assert result["cases"][0]["top1_in_expected"] is True
    assert result["cases"][0]["singleton_exact_match"] is False


def test_markdown_gate_rejects_existing_target_outside_git_index(monkeypatch, tmp_path):
    from scripts import preflight_git
    monkeypatch.setattr(preflight_git, "ROOT", tmp_path)
    document = tmp_path / "README.md"
    target = tmp_path / "summary.json"
    document.write_text("[报告](summary.json)", encoding="utf-8")
    target.write_text("{}", encoding="utf-8")
    report = preflight_git.Report()
    preflight_git.check_markdown_links(report, [document, target], [document])
    assert any("尚未纳入 Git 索引" in error for error in report.errors)
    report = preflight_git.Report()
    preflight_git.check_markdown_links(report, [document, target], [document, target])
    assert not report.errors


def test_lf_gate_checks_declared_attributes_for_new_files(monkeypatch, tmp_path):
    from scripts import preflight_git
    from types import SimpleNamespace
    monkeypatch.setattr(preflight_git, "ROOT", tmp_path)
    source = tmp_path / "new.py"
    source.write_bytes(b"pass\r\n")
    def git_attributes(command, *, input_text):
        assert input_text == "new.py\0"
        return SimpleNamespace(returncode=0, stdout="new.py\0eol\0lf\0")
    monkeypatch.setattr(preflight_git, "run", git_attributes)
    report = preflight_git.Report()
    preflight_git.check_line_endings(report, [source])
    assert report.errors
    source.write_bytes(b"pass\n")
    report = preflight_git.Report()
    preflight_git.check_line_endings(report, [source])
    assert not report.errors


def test_large_unicode_context_fits_a2a_wire_budget():
    from python_a2a import Message, MessageRole, TextContent
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "中\\\"" * 5000} for i in range(20)]
    history[-1]["content"] = "newest"
    retained = bounded_history(history)
    assert retained[-1]["content"] == "newest"
    assert history_size(retained) <= HISTORY_BYTES
    payload = SpecialistRequest(task="中" * 4000, patient_id="patient", history=retained).model_dump()
    message = Message(content=TextContent(text=json.dumps(payload, ensure_ascii=False)), role=MessageRole.USER)
    assert len(json.dumps(message.to_dict()).encode()) < 128 * 1024


def test_specialist_rejects_oversized_history():
    with pytest.raises(ValidationError, match="byte budget"):
        SpecialistRequest(task="task", patient_id="patient", history=[{"role": "user", "content": "中" * 6000}])


def test_prototypes_have_no_exact_dataset_or_regression_overlap():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "evaluation"
    def texts(path):
        return {json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    prototypes = texts(root / "intent_prototypes.jsonl")
    for dataset in [root / "intent_eval.jsonl", *(root / "datasets").glob("*.jsonl")]:
        assert not prototypes & texts(dataset), dataset.name


@pytest.mark.parametrize("field,value", [
    ("intent_bert_threshold", float("nan")), ("intent_bert_threshold", 1.1),
    ("intent_vector_threshold", -0.1), ("intent_vector_margin", -0.01),
    ("session_ttl_seconds", 0), ("checkpoint_ttl_seconds", -1),
    ("external_request_timeout", float("inf")), ("external_request_timeout", 0),
    ("agent_max_iterations", 0), ("mcp_port", 65536),
])
def test_invalid_runtime_settings_fail_before_services_start(field, value):
    from backend.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_training_rejects_normalized_train_validation_overlap():
    from scripts.train_intent_bert import check_split_isolation
    with pytest.raises(ValueError, match="重复文本"):
        check_split_isolation([("  SAME 临床文本  ", "症状评估")], [("same 临床文本", "用药审核")])
    check_split_isolation([("训练文本", "症状评估")], [("验证文本", "症状评估")])


def test_json_text_observations_are_recursively_deidentified():
    from backend.agents import deidentify_payload
    payload = [{"type": "text", "text": json.dumps({
        "name": "工具返回姓名", "nested": [{"address": "患者住址", "phone": "13812345678"}],
        "notes": json.dumps({"identity_number": "130123200001011234"}),
        "clinical_fact": "保留工具事实",
    }, ensure_ascii=False)}]
    sanitized = deidentify_payload(payload)
    decoded = json.loads(sanitized[0]["text"])
    assert decoded["name"] == "[已脱敏]"
    assert decoded["nested"][0]["address"] == "[已脱敏]"
    assert json.loads(decoded["notes"])["identity_number"] == "[已脱敏]"
    assert decoded["clinical_fact"] == "保留工具事实"
    assert "工具返回姓名" not in json.dumps(sanitized, ensure_ascii=False)


def test_identity_number_is_removed_before_phone_substrings():
    from backend.agents import deidentify
    # This ID contains a substring matching the mobile regex.
    assert deidentify("证件号130123200001011234") == "证件号[证件号已脱敏]"


@pytest.mark.parametrize("mode", ["success", "error", "restricted"])
async def test_real_mcp_adapter_preserves_failure_and_parent_scope(monkeypatch, clinical_delegation, mode):
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError
    from langchain_core.messages import AIMessage
    from backend.agents import MCPToolAgent
    from backend.config import get_settings
    from backend.service_auth import delegated_context, request_context

    server = FastMCP("offline-audit")
    calls = []
    observations = []

    @server.tool
    async def load_patient_history(patient_id: str) -> dict:
        calls.append(("load_patient_history", patient_id))
        if mode == "error":
            raise ToolError("真实数据源失败")
        return {"name": "工具返回姓名", "address": "患者住址", "clinical_fact": "保留工具事实"}

    @server.tool
    async def analyze_symptoms(patient_id: str) -> dict:
        calls.append(("analyze_symptoms", patient_id))
        return {"clinical_fact": "症状证据"}

    class FakeRuntime:
        def __init__(self, tools):
            self.tools = tools

        async def ainvoke(self, payload, config):
            if mode == "restricted":
                assert [tool.name for tool in self.tools] == ["load_patient_history"]
            tool = next(tool for tool in self.tools if tool.name == "load_patient_history")
            observations.append(await tool.ainvoke({"patient_id": "unauthorized_patient"}))
            return {"messages": [AIMessage(content="工具事实已读取")]}

    monkeypatch.setattr(get_settings(), "siliconflow_api_key", "test-key")
    monkeypatch.setattr("backend.agents.ChatOpenAI", lambda **kwargs: object())
    monkeypatch.setattr("backend.agents.MCPClient", lambda *args, **kwargs: Client(server))
    monkeypatch.setattr("backend.agents.create_agent", lambda model, tools, **kwargs: FakeRuntime(tools))
    context = {**request_context.get()}
    if mode == "restricted":
        context.update(agent="SymptomAgent", tools=["load_patient_history"])
    with delegated_context(context):
        if mode == "error":
            with pytest.raises(RuntimeError, match="工具证据不完整"):
                await MCPToolAgent("SymptomAgent").run("读取病史", "authorized_patient", {})
            assert json.loads(observations[0])["error"] == "工具调用失败"
        else:
            result = await MCPToolAgent("SymptomAgent").run("读取病史", "authorized_patient", {})
            assert result["trace"][0]["status"] == "completed"
            assert "工具返回姓名" not in observations[0]
            assert "患者住址" not in observations[0]
            assert "保留工具事实" in observations[0]
    assert calls == [("load_patient_history", "authorized_patient")]


@pytest.mark.parametrize("extra", [[], ["--clear-allergies"], ["--allergy", "new"]])
async def test_patient_import_preserves_omitted_history_and_allows_explicit_clear(monkeypatch, extra):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.database import Base
    from backend.models import PatientProfile
    from scripts import upsert_patient

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def init_db():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(upsert_patient, "SessionLocal", sessions)
    monkeypatch.setattr(upsert_patient, "init_db", init_db)
    await init_db()
    try:
        async with sessions() as db:
            db.add(PatientProfile(patient_id="audit-patient", username="audit", name="Test", age=30, gender="unknown",
                                  allergy_history='["original"]', past_medical_history='["condition"]', current_medications='["medication"]'))
            await db.commit()
        args = upsert_patient.build_parser().parse_args([
            "--patient-id", "audit-patient", "--name", "Updated", "--age", "35", "--gender", "unknown", *extra,
        ])
        await upsert_patient.upsert(args)
        async with sessions() as db:
            profile = await db.scalar(select(PatientProfile))
            expected_allergies = [] if extra == ["--clear-allergies"] else ["new"] if extra else ["original"]
            assert json.loads(profile.allergy_history) == expected_allergies
            assert json.loads(profile.past_medical_history) == ["condition"]
            assert json.loads(profile.current_medications) == ["medication"]
            assert profile.name == "Updated" and profile.age == 35
    finally:
        await engine.dispose()


async def test_invalid_patient_import_never_opens_database(monkeypatch):
    from scripts import upsert_patient
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    initialize = AsyncMock()
    monkeypatch.setattr(upsert_patient, "init_db", initialize)
    args = SimpleNamespace(patient_id="x" * 65, name="Test", age=30, gender="unknown", allergy=None, condition=None, medication=None)
    with pytest.raises(ValidationError):
        await upsert_patient.upsert(args)
    initialize.assert_not_awaited()


def test_workspace_patient_switch_discards_previous_unsaved_profile_edits(monkeypatch):
    import httpx
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    def get(url, **kwargs):
        if url.endswith("/api/patients"):
            return httpx.Response(200, json=[
                {"patient_id": pid, "name": pid, "age": 30, "gender": "unknown"} for pid in ("p1", "p2")
            ])
        if url.endswith("/profile"):
            return httpx.Response(200, json={"name": url.split("/")[-2], "age": 30, "gender": "unknown",
                                            "allergies": [], "conditions": [], "medications": []})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "get", get)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run(timeout=10)
    assert not app.exception
    app.session_state["token"] = "offline-test-token"
    app.run(timeout=10)
    app.number_input[0].set_value(45)
    next(widget for widget in app.text_input if widget.label.startswith("过敏史")).set_value("unsaved-p1-allergy")
    app.run(timeout=10)
    app.selectbox[0].set_value("p2").run(timeout=10)
    assert not app.exception
    assert app.number_input[0].value == 30
    assert next(widget for widget in app.text_input if widget.label.startswith("过敏史")).value == ""
    assert app.session_state["patient_id"] == "p2"


@pytest.mark.parametrize("entry", ["coordinator", "specialist"])
async def test_production_cloud_opt_in_is_enforced_at_model_call_entries(monkeypatch, entry):
    from backend.agents import MCPToolAgent, ModelClients
    from backend.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cloud_llm_allowed", False)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    with pytest.raises(RuntimeError, match="CLOUD_LLM_ALLOWED"):
        if entry == "coordinator":
            ModelClients()
        else:
            await MCPToolAgent("SymptomAgent").run("读取病史", "patient", {})


"""Offline regressions for authorization and active-request isolation."""

import pytest
from cachetools import TTLCache

from backend.memory import ConversationMemory
from backend.service_auth import delegated_context, issue_delegation, verify_delegation


def test_child_delegation_cannot_expand_parent_tool_scope():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-001",
        "agent": "SymptomAgent", "tools": ["load_patient_history"],
        "approved_actions": ["generate_referral"],
    }):
        parent = verify_delegation(issue_delegation(
            "medagent-a2a:SymptomAgent", "SymptomAgent", ["load_patient_history"],
        ), "medagent-a2a:SymptomAgent")
    with delegated_context(parent):
        with pytest.raises(ValueError, match="工具范围"):
            issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history", "generate_referral"])


def test_child_delegation_cannot_switch_parent_agent():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-002",
        "agent": "SymptomAgent", "tools": ["load_patient_history"],
    }):
        with pytest.raises(ValueError, match="Agent"):
            issue_delegation("medagent-mcp", "DrugAgent", ["load_patient_history"])


def test_child_delegation_can_narrow_parent_tool_scope():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-003",
        "agent": "SymptomAgent", "tools": ["load_patient_history", "generate_referral"],
        "approved_actions": ["generate_referral"],
    }):
        token = issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history"])
    assert verify_delegation(token, "medagent-mcp")["tools"] == ["load_patient_history"]


async def test_local_turn_capacity_cannot_evict_an_active_conversation():
    memory = ConversationMemory(use_redis=False)
    memory._turns = TTLCache(maxsize=1, ttl=180)
    async with memory.turn("patient", "active"):
        with pytest.raises(RuntimeError, match="容量"):
            async with memory.turn("patient", "new"):
                pytest.fail("Capacity exhaustion must not evict an active lock")
        with pytest.raises(RuntimeError, match="已有任务"):
            async with memory.turn("patient", "active"):
                pytest.fail("The original conversation must remain locked")
    async with memory.turn("patient", "new"):
        pass
