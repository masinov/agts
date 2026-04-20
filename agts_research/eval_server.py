from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid

from agts.jsonutil import to_jsonable
from agts_research.config import load_run_config
from agts_research.evaluator import submit_eval
from agts_research.storage import write_json_atomic


def evaluator_dir(run_dir: Path) -> Path:
    return run_dir / "public" / "evaluator"


def ensure_eval_server(run_dir: Path) -> None:
    cfg = load_run_config(run_dir)
    if cfg.evaluator.type == "none" or not cfg.evaluator.command:
        return
    root = evaluator_dir(run_dir)
    heartbeat = root / "server.heartbeat"
    if server_ready(run_dir):
        return

    root.mkdir(parents=True, exist_ok=True)
    process_path = root / "server.process.json"
    log_path = root / "server.log"
    log = log_path.open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "agts.cli", "research", "eval-server", str(run_dir)],
        cwd=Path.cwd(),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    process_path.write_text(
        json.dumps({"pid": process.pid, "log": str(log_path), "started_at": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _recent_heartbeat(heartbeat):
            return
        time.sleep(0.05)
    raise RuntimeError(f"evaluator server did not start: {root}")


def run_eval_server(run_dir: Path, *, idle_timeout: float = 3600.0) -> None:
    root = evaluator_dir(run_dir)
    requests = root / "requests"
    responses = root / "responses"
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    heartbeat = root / "server.heartbeat"
    last_seen = time.time()
    while time.time() - last_seen < idle_timeout:
        heartbeat.write_text(str(time.time()), encoding="utf-8")
        handled = False
        for request_path in sorted(requests.glob("*.json")):
            processing = request_path.with_suffix(".processing")
            try:
                request_path.rename(processing)
            except OSError:
                continue
            response = _handle_request(run_dir, processing)
            response_path = responses / f"{processing.stem}.json"
            write_json_atomic(response_path, response)
            try:
                processing.unlink()
            except OSError:
                pass
            handled = True
            last_seen = time.time()
        if not handled:
            time.sleep(0.1)


def request_eval(run_dir: Path, *, message: str, workdir: Path, timeout: float = 900.0) -> dict[str, object]:
    root = evaluator_dir(run_dir)
    request_id = uuid.uuid4().hex
    requests = root / "requests"
    responses = root / "responses"
    requests.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)
    request_path = requests / f"{request_id}.json"
    response_path = responses / f"{request_id}.json"
    write_json_atomic(
        request_path,
        {"type": "eval", "message": message, "workdir": str(workdir.resolve()), "created_at": time.time()},
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "evaluator server request failed")))
            return response
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for evaluator response: {response_path}")


def server_ready(run_dir: Path) -> bool:
    root = evaluator_dir(run_dir)
    return _recent_heartbeat(root / "server.heartbeat") and _recorded_process_alive(root / "server.process.json")


def _handle_request(run_dir: Path, request_path: Path) -> dict[str, object]:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request.get("type") != "eval":
            return {"ok": False, "error": f"unknown request type: {request.get('type')}"}
        cfg = load_run_config(run_dir)
        attempt = submit_eval(
            cfg=cfg,
            message=str(request["message"]),
            workdir=Path(str(request["workdir"])).resolve(),
            final=False,
            use_server=False,
        )
        return {"ok": True, "attempt": to_jsonable(attempt)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _recent_heartbeat(path: Path) -> bool:
    try:
        timestamp = float(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return time.time() - timestamp < 10.0


def _recorded_process_alive(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        command = cmdline.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
    except OSError:
        return False
    return "agts.cli" in command and "eval-server" in command
