from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_REPOSITORY_FILE_BYTES = 25 * 1024 * 1024

FORBIDDEN_TRACKED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "artifacts",
    "reports",
    "models",
    "checkpoints",
    "runs",
    "wandb",
    "mlruns",
}
FORBIDDEN_TRACKED_NAMES = {".env", "secrets.toml", "medagent.db"}
FORBIDDEN_SUFFIXES = {
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

SECRET_PATTERNS = [
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


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
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
    required = [Path(".env"), Path("medagent.db"), Path("artifacts")]
    missing: list[str] = []
    for relative in required:
        result = run(["git", "check-ignore", "-q", "--", relative.as_posix()])
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
            or relative.suffix.lower() in FORBIDDEN_SUFFIXES
            or lowered_parts.intersection(FORBIDDEN_TRACKED_PARTS)
        ):
            problems.append(relative.as_posix())
    if problems:
        report.error("不应被 Git 跟踪的文件：" + ", ".join(sorted(problems)))
    else:
        report.ok("Git 索引中没有本地密钥、数据库、模型权重或生成产物")


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
    pattern = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/](?:Users|pythonProject|python_envs)[\\/]")
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


def run_quality_commands(report: Report) -> None:
    checks = [
        ([sys.executable, "-m", "pytest", "-q"], "pytest"),
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
    check_secret_content(report, candidates)
    check_large_files(report, candidates)
    check_env_template(report)
    check_python_syntax(report, candidates)
    check_absolute_local_paths(report, candidates)
    if not args.quick:
        run_quality_commands(report)
    return print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
