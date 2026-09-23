"""Start MCP, three A2A servers, FastAPI and the Streamlit UI."""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_config import missing_configuration

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    missing = missing_configuration()
    if missing:
        print("请先填写 .env：")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)
    commands = [
        [sys.executable, "-m", "backend.mcp_tools"],
        [sys.executable, "-m", "backend.a2a_server", "--agent", "symptom", "--port", "8011"],
        [sys.executable, "-m", "backend.a2a_server", "--agent", "drug", "--port", "8012"],
        [sys.executable, "-m", "backend.a2a_server", "--agent", "guide", "--port", "8013"],
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--browser.gatherUsageStats", "false"],
    ]
    processes = []
    try:
        for command in commands:
            processes.append(subprocess.Popen(command, cwd=ROOT))
        while True:
            for command, process in zip(commands, processes, strict=True):
                exit_code = process.poll()
                if exit_code is not None:
                    print(f"服务异常退出 ({exit_code})：{' '.join(command)}")
                    raise SystemExit(exit_code or 1)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
