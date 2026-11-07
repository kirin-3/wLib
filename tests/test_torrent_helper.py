import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _spawn(tmp_path: Path) -> subprocess.Popen[str]:
    env = {**os.environ, "WLIB_DATA_DIR": str(tmp_path)}
    return subprocess.Popen(
        [sys.executable, str(REPO / "main.py"), "--torrent-helper"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(REPO),
    )


def test_helper_answers_hello_and_exits_on_eof(tmp_path: Path) -> None:
    proc = _spawn(tmp_path)
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({"cmd": "hello"}) + "\n")
    proc.stdin.flush()
    ready = json.loads(proc.stdout.readline())
    assert ready["event"] == "ready" and ready["port"] > 0
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0
    proc.stdout.close()
    assert proc.stderr is not None
    proc.stderr.close()
