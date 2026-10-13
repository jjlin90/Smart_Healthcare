from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shlex
import subprocess
import tempfile
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_REPOSITORY_FILE_BYTES = 25 * 1024 * 1024
# Extend this explicit policy when adding a repository development tool.
DEV_ONLY_TOOLS = frozenset({'pytest', 'pytest-asyncio', 'locust', 'pyyaml'})
# Public, sanitized rewrite record; update deliberately after a new history rewrite.
HISTORY_REWRITE_MAP_SHA256 = "7385d5d789d348389b03f3851695209960adf6962f2f49e02cb6345451e893f2"

FORBIDDEN_TRACKED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "artifacts",
    "output",
    ".mimosa",
    "reports",
    "models",
    "checkpoints",
    "runs",
    "wandb",
    "mlruns",
}
FORBIDDEN_TRACKED_NAMES = {".env", "secrets.toml", "medagent.db"}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".pdf", ".docx", ".doc", ".bundle", ".bak", ".zip", ".onnx", ".bin",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".sqlite",
    ".sqlite3",
    ".gguf",
    ".safetensors",
    ".ckpt",
    ".pth",
    ".pt",
}

REQUIRED_TRACKED_PATHS = {
    "docs/git-history-rewrite-map.json",
    "tests/test_history_rewrite_map.py",
    "scripts/record_document_checks.py",
    "tests/test_record_document_checks.py",
    "requirements-dev.txt",
    "backend/safety.py",
    "tests/test_safety.py",
    "backend/intent_fusion.py",
    "tests/test_intent_fusion.py",
    "scripts/intent_metrics.py",
    "scripts/build_multilabel_intent_dataset.py",
    "docs/intent-fusion.md",
    "evaluation/datasets/multilabel_v4/manifest.json",
    "evaluation/datasets/multilabel_v4/intent_train.jsonl",
    "evaluation/datasets/multilabel_v4/intent_validation.jsonl",
    "evaluation/datasets/multilabel_v4/intent_test.jsonl",
    "evaluation/bert_multilabel_test_summary.json",
    "evaluation/bert_multilabel_challenge_summary.json",
    "evaluation/intent_fusion_summary.json",
    "evaluation/intent_fusion_402_historical_summary.json",
    "evaluation/intent_review_repeat_summary.json",
    "evaluation/model_artifacts_v3_historical.json",
    "backend/metrics_bridge.py",
    "tests/test_monitoring.py",
    "docs/monitoring-verification.md",
    "backend/a2a_context.py",
    ".githooks/run-preflight",
    "tests/test_review_fixes.py",
    "docs/audit-2026-09-28.md",
    "docs/audit-2026-10-07.md",
    "constraints.txt",
    "backend/access.py",
    "backend/service_auth.py",
    "backend/mcp_security.py",
    "backend/rate_limit.py",
    "migrations/versions/20260926_02_tool_executions.py",
    "tests/test_enterprise_boundaries.py",
    "tests/test_migrations.py",
    "alembic.ini",
    "migrations/env.py",
    "migrations/script.py.mako",
    "migrations/versions/20260921_01_internal_staff_access.py",
    "evaluation/intent_prototypes.jsonl",
    "evaluation/datasets/manifest.json",
    "evaluation/datasets/intent_train.jsonl",
    "evaluation/datasets/intent_validation.jsonl",
    "evaluation/datasets/intent_test.jsonl",
    "evaluation/datasets/intent_challenge.jsonl",
    "scripts/build_intent_dataset.py",
    "scripts/calibrate_intent_vector.py",
    "scripts/create_staff.py",
    "scripts/evaluate_intent_bert.py",
    "scripts/train_intent_bert.py",
    "scripts/verify_model_artifacts.py",
    "evaluation/model_artifacts.json",
    "evaluation/bert_historical_summary.json",
    "evaluation/vector_challenge_summary.json",
    "loadtests/results/2026-09-20_health_stats.csv",
    "loadtests/results/2026-09-20_health_stats_history.csv",
    "scripts/upsert_patient.py",
    "tests/conftest.py",
    "tests/test_intent_dataset.py",
    "tests/test_internal_access.py",
}

SECRET_PATTERNS = [
    ("LangSmith Token", re.compile(r"\blsv2_[A-Za-z0-9_-]{20,}\b")),
    ("GitHub Fine-grained Token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI/SiliconFlow 风格密钥", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("AWS Access Key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("疑似 JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("私钥内容", re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.passed: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self, message: str) -> None:
        self.passed.append(message)


def run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        input=input_text,
        check=False,
    )


def is_git_repository() -> bool:
    result = run(["git", "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_paths(command: list[str], report: Report, label: str) -> list[Path]:
    result = run(command)
    if result.returncode != 0:
        report.error(f"无法读取 Git {label}：{result.stderr.strip()}")
        return []
    return [ROOT / name for name in result.stdout.split("\0") if name]


def check_required_ignores(report: Report) -> None:
    # CI checks out only tracked files, so these directories may not exist.
    # Probe a child path to verify the directory rule in either environment.
    required = [
        Path(".env"),
        Path("medagent.db"),
        Path("artifacts/__preflight_probe__"),
        Path("output/__preflight_probe__"),
        Path(".mimosa/__preflight_probe__"),
    ]
    missing: list[str] = []
    for relative in required:
        result = run(["git", "check-ignore", "-q", "--no-index", "--", relative.as_posix()])
        if result.returncode != 0:
            missing.append(relative.as_posix())
    if missing:
        report.error("以下本地敏感/生成路径未被忽略：" + ", ".join(missing))
    else:
        report.ok("敏感配置、数据库和生成产物均命中 .gitignore")


def check_forbidden_files(report: Report, tracked: list[Path]) -> None:
    problems: list[str] = []
    for path in tracked:
        relative = path.relative_to(ROOT)
        lowered_parts = {part.lower() for part in relative.parts}
        if (
            relative.name.lower() in FORBIDDEN_TRACKED_NAMES
            or (relative.name.startswith(".env.") and relative.name != ".env.example")
            or (relative.parts[0] == "scripts" and "resume" in relative.name.lower() and relative.suffix.lower() == ".py")
            or (relative.suffix.lower() == ".html" and ("resume" in relative.name.lower() or "简历" in relative.name or "算法工程师" in relative.name))
            or relative.suffix.lower() in FORBIDDEN_SUFFIXES
            or lowered_parts.intersection(FORBIDDEN_TRACKED_PARTS)
        ):
            problems.append(relative.as_posix())
    if problems:
        report.error("不应被 Git 跟踪的文件：" + ", ".join(sorted(problems)))
    else:
        report.ok("Git 索引中没有本地密钥、数据库、模型权重或生成产物")


def check_history_artifacts(report: Report) -> None:
    result = run([
        "git", "log", "--all", "--format=", "--name-only", "--",
        "output", ".mimosa", ":(glob)scripts/*resume*.py",
    ])
    if result.returncode != 0:
        report.warning("无法核查 Git 历史中的本地生成产物")
    elif result.stdout.strip():
        report.warning("Git 历史包含简历或运行状态文件；取消跟踪不会删除既有提交，推送前需核查远端与历史")
    else:
        report.ok("Git 引用可达历史未发现简历或运行状态文件（不含 reflog、不可达对象及私有备份）")


def check_required_project_files_tracked(report: Report, tracked: list[Path]) -> None:
    tracked_names = {path.relative_to(ROOT).as_posix() for path in tracked}
    missing = sorted(REQUIRED_TRACKED_PATHS - tracked_names)
    if missing:
        report.error("实现或复现所需文件尚未加入 Git 索引：" + ", ".join(missing))
    elif check_history_rewrite_map(report):
        report.ok("迁移、数据集、模型脚本、关键测试和脱敏 SHA 映射均已入索引，映射内容及新提交可达性通过")


def check_history_rewrite_map(report: Report) -> bool:
    """Validate the delivered rewrite record without requiring private old objects."""
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("重复 JSON 键")
            value[key] = item
        return value

    try:
        path = ROOT / "docs/git-history-rewrite-map.json"
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("无效 schema_version")
        mapping = data.get("commits")
        if not isinstance(mapping, dict) or len(mapping) != 26:
            raise ValueError("必须包含本次改写的 26 组 SHA")
        if any(not isinstance(x, str) or not re.fullmatch(r"[a-f0-9]{40}", x)
               for pair in mapping.items() for x in pair):
            raise ValueError("必须使用完整小写 SHA-1")
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("新 SHA 不得重复")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != HISTORY_REWRITE_MAP_SHA256:
            raise ValueError("记录与已核验的脱敏映射指纹不同；再次改写历史需同步映射和校验基准")
        history = run(["git", "rev-list", "HEAD"])
        if history.returncode or not set(mapping.values()) <= set(history.stdout.splitlines()):
            raise ValueError("新 SHA 未全部位于当前 HEAD 历史；浅克隆需补齐历史")
    except (OSError, UnicodeError, ValueError) as exc:
        report.error(f"脱敏 SHA 映射校验失败：{exc}")
        return False
    return True


def check_local_imports_tracked(report: Report, tracked: list[Path], candidates: list[Path] | None = None) -> None:
    """Check candidate Python sources, static imports and literal launch argv."""
    tracked_names = {path.relative_to(ROOT).as_posix() for path in tracked}
    missing: set[str] = set()
    for source in candidates if candidates is not None else tracked:
        if source.suffix != ".py" or not source.is_file():
            continue
        source_name = source.relative_to(ROOT).as_posix()
        if source_name not in tracked_names and source.relative_to(ROOT).parts[0] in {"backend", "scripts", "tests", "migrations"}:
            missing.add(f"未跟踪 Python 文件：{source_name}")
        try:
            tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeError):
            continue  # The syntax check reports malformed source separately.
        package = list(source.relative_to(ROOT).parent.parts)
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = package[:len(package) - node.level + 1] if node.level else []
                if node.module:
                    base += node.module.split(".")
                modules = [".".join(base)] + [".".join([*base, alias.name]) for alias in node.names if alias.name != "*"]
            elif isinstance(node, (ast.List, ast.Tuple)):
                # Literal subprocess argv, including `python -m uvicorn module:app`.
                values = [item.value if isinstance(item, ast.Constant) and isinstance(item.value, str) else None for item in node.elts]
                for index, value in enumerate(values[:-1]):
                    if value == "-m" and values[index + 1]:
                        modules.append(values[index + 1])
                        if values[index + 1] == "uvicorn" and index + 2 < len(values) and values[index + 2]:
                            modules.append(values[index + 2].split(":", 1)[0])
            for module in modules:
                if not module:
                    continue
                relative = Path(*module.split("."))
                for dependency in (ROOT / relative.with_suffix(".py"), ROOT / relative / "__init__.py"):
                    if dependency.is_file() and dependency.relative_to(ROOT).as_posix() not in tracked_names:
                        missing.add(f"{source.relative_to(ROOT).as_posix()} -> {dependency.relative_to(ROOT).as_posix()}")
    if missing:
        report.error("候选 Python 文件或其本地依赖未加入 Git 索引：" + ", ".join(sorted(missing)))
    else:
        report.ok("项目代码目录 Python 文件及候选代码的静态导入、字面量模块启动依赖均已加入 Git 索引")


def text_content(path: Path) -> str | None:
    if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
        return None
    raw = path.read_bytes()
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def check_secret_content(report: Report, candidates: list[Path]) -> None:
    findings: list[str] = []
    scanned = 0
    for path in candidates:
        text = text_content(path)
        if text is None:
            continue
        scanned += 1
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}（{label}）")
    if findings:
        report.error("发现疑似敏感内容（仅报告位置，不输出密钥）：" + "; ".join(findings))
    else:
        report.ok(f"敏感内容扫描通过（{scanned} 个文本文件）")


def check_large_files(report: Report, candidates: list[Path]) -> None:
    oversized = [
        f"{path.relative_to(ROOT).as_posix()} ({path.stat().st_size / 1024 / 1024:.1f} MiB)"
        for path in candidates
        if path.is_file() and path.stat().st_size > MAX_REPOSITORY_FILE_BYTES
    ]
    if oversized:
        report.error("发现超过 25 MiB 的候选文件：" + ", ".join(oversized))
    else:
        report.ok("候选文件大小检查通过")


def check_index_content(report: Report, tracked: list[Path]) -> None:
    """Scan staged blobs too: a clean working copy can hide an older staged secret."""
    failures = []
    for path in tracked:
        relative = path.relative_to(ROOT).as_posix()
        result = run(["git", "show", f":{relative}"])
        if result.returncode:
            failures.append(f"{relative}（无法读取索引内容）")
            continue
        if "\0" in result.stdout:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(result.stdout):
                failures.append(f"{relative}（{label}）")
    if failures:
        report.error("暂存内容检查失败（不输出密钥）：" + "; ".join(failures))
    else:
        report.ok("Git 索引内容敏感扫描通过（与工作区分别检查）")


def dotenv_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.add(key)
    return keys


def check_env_template(report: Report) -> None:
    template = ROOT / ".env.example"
    if not template.exists():
        report.error("缺少 .env.example")
        return
    local = ROOT / ".env"
    missing = sorted(dotenv_keys(local) - dotenv_keys(template)) if local.exists() else []
    if missing:
        report.error(".env.example 缺少本地配置项：" + ", ".join(missing))
    else:
        report.ok(".env.example 已覆盖本地配置项名称")


def check_python_syntax(report: Report, candidates: list[Path]) -> None:
    failures: list[str] = []
    checked = 0
    for path in candidates:
        if path.suffix.lower() != ".py" or not path.is_file():
            continue
        checked += 1
        try:
            compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        except (SyntaxError, UnicodeError) as exc:
            failures.append(f"{path.relative_to(ROOT).as_posix()}: {exc}")
    if failures:
        report.error("Python 语法检查失败：" + "; ".join(failures))
    else:
        report.ok(f"Python 语法检查通过（{checked} 个文件）")


def check_absolute_local_paths(report: Report, candidates: list[Path]) -> None:
    pattern = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/](?:Users|pythonProject|python_envs)(?:[\\/]|(?=\s|$))")
    findings: list[str] = []
    for path in candidates:
        text = text_content(path)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                findings.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}")
    if findings:
        report.error("发现不可移植的本机绝对路径：" + ", ".join(findings))
    else:
        report.ok("未发现会泄露本机目录的绝对路径")


def _markdown_without_fences(body: str) -> str:
    lines = []
    fence = None
    for line in body.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            lines.append("\n" if line.endswith("\n") else "")
        elif marker:
            fence = marker[1]
            lines.append("\n" if line.endswith("\n") else "")
        else:
            lines.append(line)
    return "".join(lines)


def _markdown_headings(body: str) -> tuple[set[str], set[str]]:
    titles = re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", _markdown_without_fences(body), re.M)
    anchors: set[str] = set()
    for title in titles:
        plain = re.sub(r"<[^>]+>", "", title).lower()
        slug = "".join(char for char in plain if char in "-_ " or unicodedata.category(char)[0] in "LNM").replace(" ", "-")
        unique, index = slug, 0
        while unique in anchors:
            index += 1
            unique = f"{slug}-{index}"
        anchors.add(unique)
    return set(titles), anchors


def check_markdown_links(report: Report, candidates: list[Path], tracked: list[Path] | None = None) -> None:
    """Check rendered inline and Wiki local links in working-tree Markdown."""
    documents = [path for path in candidates if path.suffix.lower() == ".md" and path.is_file()]
    findings: list[str] = []
    tracked_targets = {path.resolve() for path in tracked} if tracked is not None else None
    heading_cache = {}
    for document in documents:
        body = _markdown_without_fences(document.read_text(encoding="utf-8-sig"))
        # Inline code can contain illustrative links; retain line numbers.
        body = re.sub(r"(`+)[^\n]*?\1", lambda match: " " * len(match[0]), body)
        links = [(match.start(), match[1], False) for match in re.finditer(r'\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+"[^"\n]*")?\s*\)', body)]
        links += [(match.start(), match[1].split("|", 1)[0], True) for match in re.finditer(r"\[\[([^\]\n]+)\]\]", body)]
        for position, destination, wiki in links:
            destination = destination.strip().strip("<>")
            parsed = urlsplit(destination)
            if parsed.scheme or destination.startswith("//"):
                continue
            file, anchor = unquote(parsed.path), unquote(parsed.fragment)
            if wiki and file and not Path(file).suffix:
                file += ".md"
            target = (document.parent / file).resolve() if file else document.resolve()
            line = body[:position].count("\n") + 1
            reason = None
            if not target.is_relative_to(ROOT.resolve()):
                reason = "目标位于仓库之外"
            elif not target.is_file():
                reason = "目标文件不存在"
            elif tracked_targets is not None and target not in tracked_targets:
                reason = "目标尚未纳入 Git 索引，克隆后不可用"
            elif anchor and target.suffix.lower() == ".md":
                if target not in heading_cache:
                    heading_cache[target] = _markdown_headings(target.read_text(encoding="utf-8-sig"))
                titles, anchors = heading_cache[target]
                if anchor not in (titles if wiki else anchors):
                    reason = "章节锚点不存在"
            if reason:
                findings.append(f"{document.relative_to(ROOT).as_posix()}:{line} {destination}（{reason}）")
    if findings:
        report.error("工作区 Markdown 本地链接检查失败：" + "; ".join(findings))
    else:
        report.ok(f"工作区 Markdown 本地文件与章节链接检查通过（{len(documents)} 份文档）")


def check_line_endings(report: Report, candidates: list[Path]) -> None:
    result = run(["git", "check-attr", "-z", "--stdin", "eol"],
                 input_text="\0".join(path.relative_to(ROOT).as_posix() for path in candidates) + "\0")
    if result.returncode:
        report.error("无法检查 Git 换行属性")
        return
    attributes = result.stdout.split("\0")
    failures = [name for name, _, value in zip(attributes[::3], attributes[1::3], attributes[2::3])
                if value == "lf" and (ROOT / name).is_file() and b"\r" in (ROOT / name).read_bytes()]
    if failures:
        report.error("声明 LF 的工作区文件含 CR：" + ", ".join(failures))
    else:
        report.ok("声明 LF 的工作区文件换行一致")


def check_kubernetes_intent_config(report: Report) -> None:
    """Inspect the deployable ConfigMap, independently of the local .env."""
    try:
        import yaml
    except ImportError:
        report.error(
            "缺少开发依赖 PyYAML；请用运行预检/提交钩子的同一 Python 环境执行："
            "python -m pip install -r requirements-dev.txt -c constraints.txt"
        )
        return
    try:
        documents = list(yaml.safe_load_all((ROOT / "deploy/k8s/medagent-api.yaml").read_text(encoding="utf-8")))
        configs = [item for item in documents if isinstance(item, dict)
                   and item.get("kind") == "ConfigMap"
                   and item.get("metadata", {}).get("name") == "medagent-config"]
        if len(configs) != 1:
            raise ValueError("必须包含唯一的 medagent-config")
        data = configs[0]["data"]
        required = {
            "INTENT_BERT_MODEL_PATH": "/models/medical_intent_bert_multilabel_v4",
            "INTENT_VECTOR_MODEL_PATH": "/models/bge-m3",
            "INTENT_PROTOTYPES_PATH": "/app/evaluation/intent_prototypes.jsonl",
        }
        for name, expected in required.items():
            if data.get(name) != expected:
                raise ValueError(f"{name} 与当前交付模型/原型不一致")
        if float(data["INTENT_BERT_THRESHOLD"]) != 0.5:
            raise ValueError("多标签 BERT 默认阈值应为 0.5，不能沿用旧 Softmax 阈值")
        deployments = [item for item in documents if isinstance(item, dict) and item.get("kind") == "Deployment"]
        for name in ("medagent-api-stable", "medagent-api-canary"):
            matches = [item for item in deployments if item.get("metadata", {}).get("name") == name]
            if len(matches) != 1:
                raise ValueError(f"缺少唯一的 {name}")
            spec = matches[0]["spec"]["template"]["spec"]
            api = next(item for item in spec["containers"] if item["name"] == "api")
            if not any(item.get("configMapRef", {}).get("name") == "medagent-config" for item in api.get("envFrom", [])):
                raise ValueError(f"{name} 未使用当前 ConfigMap")
            if any(str(item.get("name", "")).startswith("INTENT_") for item in api.get("env", [])):
                raise ValueError(f"{name} 的 env 覆盖意图配置，请统一维护 ConfigMap")
            if not any(item.get("name") == "intent-models" and item.get("mountPath") == "/models"
                       and item.get("readOnly") is True for item in api.get("volumeMounts", [])):
                raise ValueError(f"{name} 缺少只读模型挂载")
            if not any(item.get("name") == "intent-models" and item.get("persistentVolumeClaim", {}).get("claimName") == "medagent-intent-models"
                       for item in spec.get("volumes", [])):
                raise ValueError(f"{name} 模型 PVC 配置不一致")
    except Exception as exc:
        report.error(f"Kubernetes 意图模型配置检查失败：{exc}")
    else:
        report.ok("Kubernetes ConfigMap 与 stable/canary 模型挂载检查通过（v4 多标签 BERT、0.5 默认阈值）")


def check_direct_dependency_constraints(report: Report) -> None:
    import tomllib
    try:
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        declarations = list(project['dependencies'])
        for group in project.get('optional-dependencies', {}).values():
            declarations.extend(group)
        for name in ('requirements.txt', 'requirements-dev.txt'):
            declarations.extend(line.strip() for line in (ROOT / name).read_text(encoding='utf-8').splitlines()
                                if line.strip() and not line.lstrip().startswith(('#', '-')))
        normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
        names = {normalize(re.match(r'[A-Za-z0-9_.-]+', line)[0]) for line in declarations}
        pins = set()
        for line in (ROOT / 'constraints.txt').read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'\s*([A-Za-z0-9_.-]+)==([^\s*]+)\s*', line)
            if match:
                pins.add(normalize(match[1]))
        missing = sorted(names - pins)
        if missing:
            raise ValueError('未固定的直接依赖：' + ', '.join(missing))
    except Exception as exc:
        report.error(f'直接依赖约束检查失败：{exc}')
    else:
        report.ok('运行与开发直接依赖均有精确版本约束（未校验声明范围兼容性）')


def check_dependency_classification(report: Report) -> None:
    """Compare this repository's plain name/extras/version declarations using stdlib.

    Reject unsupported pip directives rather than silently missing dependencies.
    Installation resolution remains responsible for version compatibility.
    """
    import tomllib

    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())

    def declarations(lines: list[str], label: str) -> dict:
        result = {}
        for line in lines:
            value = line.strip()
            if not value or value.startswith('#'):
                continue
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[([A-Za-z0-9_.,-]+)\])?\s*(.*)', value)
            if not match:
                raise ValueError(f'{label} 存在不支持的依赖声明：{value}')
            name = normalize(match[1])
            extras = tuple(sorted(normalize(extra) for extra in (match[2] or '').split(',') if extra))
            versions = tuple(sorted(part.strip().replace(' ', '') for part in match[3].split(',') if part.strip()))
            if any(not re.fullmatch(r'(===|~=|==|!=|<=|>=|<|>)[A-Za-z0-9.*+!_-]+', part) for part in versions):
                raise ValueError(f'{label} 存在不支持的版本/标记声明：{value}')
            if name in result:
                raise ValueError(f'{label} 重复声明依赖：{name}')
            result[name] = (extras, versions)
        return result

    try:
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        runtime = declarations((ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines(), 'requirements.txt')
        dev_lines = (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8').splitlines()
        includes = [line for line in dev_lines if re.match(r'^\s*-r\s+', line)]
        if len(includes) != 1 or (ROOT / includes[0].strip().split(maxsplit=1)[1]).resolve() != (ROOT / 'requirements.txt').resolve():
            raise ValueError('requirements-dev.txt 必须且只能一次通过 -r requirements.txt 包含运行依赖')
        dev = declarations([line for line in dev_lines if line not in includes], 'requirements-dev.txt')
        core = declarations(project['dependencies'], 'pyproject core')
        extra = declarations(project.get('optional-dependencies', {}).get('dev', []), 'pyproject dev')
        tools = DEV_ONLY_TOOLS
        misplaced = sorted(tools & (runtime.keys() | core.keys()))
        if misplaced:
            raise ValueError('开发工具不能进入运行依赖：' + ', '.join(misplaced))
        for label, actual, expected in (('运行依赖', runtime, core), ('开发依赖', dev, extra)):
            mismatched = sorted(name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))
            if mismatched:
                raise ValueError(f'{label}声明与 pyproject 不一致：' + ', '.join(mismatched))
        overlap = sorted(runtime.keys() & dev.keys())
        if overlap:
            raise ValueError('运行与开发依赖重复分类：' + ', '.join(overlap))
        missing = sorted(tools - dev.keys())
        if missing:
            raise ValueError('开发依赖缺少仓库工具：' + ', '.join(missing))
    except Exception as exc:
        report.error(f'依赖分类检查失败：{exc}')
    else:
        report.ok('运行/开发依赖声明与 pyproject 一致，受管开发工具未混入运行依赖')


def check_dependency_install_entries(report: Report) -> None:
    """Check visible pip install commands in the current Docker/verification CI."""
    def installs(commands: list[str], label: str, requirement: str) -> None:
        count = 0
        for command in commands:
            lexer = shlex.shlex(command.replace('\\\n', ' '), posix=True, punctuation_chars=';&|\n')
            lexer.whitespace = ' \t\r'
            lexer.whitespace_split = True
            segments = [[]]
            for token in lexer:
                if token and all(char in ';&|\n' for char in token):
                    segments.append([])
                else:
                    segments[-1].append(token)
            for tokens in segments:
                # The project uses pip or python -m pip, without shell indirection.
                if len(tokens) >= 2 and tokens[:2] in (['pip', 'install'], ['pip3', 'install']):
                    args = tokens[2:]
                elif len(tokens) >= 4 and re.fullmatch(r'python(?:3(?:\.\d+)?)?', tokens[0]) and tokens[1:4] == ['-m', 'pip', 'install']:
                    args = tokens[4:]
                else:
                    continue
                count += 1
                requirements, constraints, direct = [], [], []
                index = 0
                while index < len(args):
                    token = args[index]
                    if token in ('-r', '--requirement', '-c', '--constraint'):
                        index += 1
                        if index >= len(args):
                            raise ValueError(f'{label} 安装命令缺少 {token} 参数')
                        (requirements if token in ('-r', '--requirement') else constraints).append(args[index])
                    elif token.startswith(('--requirement=', '--constraint=')):
                        (requirements if token.startswith('--requirement=') else constraints).append(token.split('=', 1)[1])
                    elif token.startswith(('-r', '-c')) and len(token) > 2:
                        (requirements if token.startswith('-r') else constraints).append(token[2:])
                    elif token not in ('--no-cache-dir', '--upgrade', '--quiet', '-q', '--disable-pip-version-check'):
                        direct.append(token)
                    index += 1
                canonical = lambda paths: [(ROOT / path).resolve() for path in paths]
                if canonical(requirements) != [(ROOT / requirement).resolve()] or canonical(constraints) != [(ROOT / 'constraints.txt').resolve()] or direct:
                    raise ValueError(f'{label} 必须仅通过 -r {requirement} -c constraints.txt 安装，不能追加其他清单或直接包')
        if not count:
            raise ValueError(f'{label} 缺少可检查的 pip install 入口（不支持脚本或变量间接安装）')

    try:
        import yaml
    except ImportError:
        report.error('依赖安装入口检查缺少开发依赖 PyYAML；请执行 python -m pip install -r requirements-dev.txt -c constraints.txt')
        return
    try:
        docker = (ROOT / 'Dockerfile').read_text(encoding='utf-8').replace('\\\n', ' ')
        installs(re.findall(r'^RUN\s+(.+)$', docker, flags=re.M | re.I), '生产 Docker', 'requirements.txt')
        workflow = yaml.safe_load((ROOT / '.github/workflows/verify.yml').read_text(encoding='utf-8'))
        commands = [step['run'] for job in workflow['jobs'].values() for step in job['steps'] if 'run' in step]
        installs(commands, '验证 CI', 'requirements-dev.txt')
    except Exception as exc:
        report.error(f'依赖安装入口检查失败：{exc}')
    else:
        report.ok('生产 Docker/验证 CI 的直接 pip 安装入口使用运行/开发清单及 constraints')


def check_new_staged_content(report: Report) -> None:
    added = run(['git', 'diff', '--cached', '--name-only', '--diff-filter=A', '-z'])
    changed = run(['git', 'diff', '--name-only', '-z'])
    if added.returncode or changed.returncode:
        report.error('无法核对新增文件的暂存内容')
        return
    stale = sorted(set(added.stdout.split('\0')) & set(changed.stdout.split('\0')) - {''})
    if stale:
        report.error('新增文件暂存内容落后于工作区，请 git add 更新：' + ', '.join(stale))
    else:
        report.ok('新增文件的暂存内容与工作区一致（不代替完整暂存快照测试）')


def run_quality_commands(report: Report) -> None:
    # Use an isolated base instead of sharing pytest-of-USER across sandbox
    # sessions. testpaths restricts collection, not teardown permissions.
    with tempfile.TemporaryDirectory(prefix="medagent-preflight-") as temporary:
        _run_quality_commands(report, str(Path(temporary) / "pytest"))


def _run_quality_commands(report: Report, basetemp: str) -> None:
    checks = [
        ([sys.executable, "-m", "pytest", "-q", "--basetemp", basetemp], "pytest"),
        ([sys.executable, "-m", "pip", "check"], "pip check"),
    ]
    for command, label in checks:
        result = run(command)
        if result.returncode == 0:
            detail = result.stdout.strip().splitlines()
            report.ok(f"{label} 通过" + (f"：{detail[-1]}" if detail else ""))
        else:
            output = (result.stdout + "\n" + result.stderr).strip()
            tail = " | ".join(output.splitlines()[-8:])
            report.error(f"{label} 失败：{tail}")


def print_report(report: Report) -> int:
    print("\nGitHub 上传前检查")
    print("=" * 48)
    for item in report.passed:
        print(f"[PASS] {item}")
    for item in report.warnings:
        print(f"[WARN] {item}")
    for item in report.errors:
        print(f"[FAIL] {item}")
    print("-" * 48)
    if report.errors:
        print(f"结论：不建议上传，存在 {len(report.errors)} 项失败。")
        return 1
    if report.warnings:
        print("结论：当前索引与工作区检查通过；上述历史风险仍需处理或确认。")
    else:
        print("结论：检查通过，可以进入提交/推送流程。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub 上传前安全与质量检查")
    parser.add_argument("--quick", action="store_true", help="跳过 pytest 与 pip check")
    args = parser.parse_args()

    report = Report()
    if not is_git_repository():
        report.error("当前目录不是 Git 仓库，请先运行 git init")
        return print_report(report)

    candidates = git_paths(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        report,
        "候选文件",
    )
    tracked = git_paths(["git", "ls-files", "-z"], report, "已跟踪文件")
    check_required_ignores(report)
    check_forbidden_files(report, tracked)
    check_history_artifacts(report)
    check_required_project_files_tracked(report, tracked)
    check_local_imports_tracked(report, tracked, candidates)
    check_secret_content(report, candidates)
    check_index_content(report, tracked)
    check_large_files(report, candidates)
    check_env_template(report)
    check_python_syntax(report, candidates)
    check_absolute_local_paths(report, candidates)
    check_markdown_links(report, candidates, tracked)
    check_line_endings(report, candidates)
    check_kubernetes_intent_config(report)
    check_direct_dependency_constraints(report)
    check_dependency_classification(report)
    check_dependency_install_entries(report)
    check_new_staged_content(report)
    if not args.quick:
        run_quality_commands(report)
    return print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
