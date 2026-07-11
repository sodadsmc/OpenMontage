"""Render + machine-QC as a dashboard job (the final mile in the cockpit).

POST /projects/{pid}/render kicks a background thread: build_v6 -> render_v6
(both with .env loaded and their own hard sync gates) -> lib.render_qc over
the fresh render -> report stored at artifacts/render_qc_report.json. One job
at a time per project; the job dies with the server process (a dashboard
restart mid-render just means re-running the render — every stage is
idempotent/resumable).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

PROJECTS_DIR = Path("projects")
_PY = os.environ.get("OPENMONTAGE_PYTHON") or \
    r"C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe"

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}


def _driver(pid: str, name: str) -> Path | None:
    hits = sorted((PROJECTS_DIR / pid).glob(f"script_*/{name}"))
    return hits[-1] if hits else None


def start_render(pid: str) -> dict:
    with _LOCK:
        live = next((j for j in _JOBS.values()
                     if j["project_id"] == pid and j["status"] in ("queued", "running")),
                    None)
        if live:
            return {"status": "busy", "job_id": live["job_id"]}
        build = _driver(pid, "build_v6.py")
        render = _driver(pid, "render_v6.py")
        if not (build and render):
            return {"status": "blocked",
                    "error": f"no build_v6.py/render_v6.py under projects/{pid}/script_*/"}
        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "job_id": job_id, "project_id": pid, "status": "queued",
            "stage": None, "created_ts": time.time(), "ended_ts": None,
            "error": None, "qc_findings": None, "render": None,
        }
    t = threading.Thread(target=_run, args=(pid, job_id, build, render), daemon=True)
    t.start()
    return {"status": "queued", "job_id": job_id}


def _run(pid: str, job_id: str, build: Path, render: Path) -> None:
    job = _JOBS[job_id]
    job["status"] = "running"
    env = dict(os.environ)
    try:
        for line in open(".env", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.split("#")[0].strip()
                if v:
                    env.setdefault(k, v)
    except OSError:
        pass
    env["PYTHONUNBUFFERED"] = "1"
    try:
        for stage, script in (("build", build), ("render", render)):
            job["stage"] = stage
            r = subprocess.run([_PY, str(script)], env=env,
                               capture_output=True, text=True)
            if r.returncode != 0:
                job["status"] = "failed"
                job["error"] = f"{stage} failed: " + (r.stdout + r.stderr)[-600:]
                return
        job["stage"] = "qc"
        report_path = PROJECTS_DIR / pid / "artifacts" / "render_qc_report.json"
        r = subprocess.run([_PY, "-m", "lib.render_qc", pid,
                            "--json", str(report_path)],
                           env=env, capture_output=True, text=True)
        findings = None
        if report_path.is_file():
            try:
                rep = json.loads(report_path.read_text(encoding="utf-8"))
                findings = len(rep.get("findings", []))
                job["render"] = rep.get("render")
            except json.JSONDecodeError:
                pass
        if r.returncode != 0 and findings is None:
            job["status"] = "failed"
            job["error"] = "render_qc failed: " + (r.stdout + r.stderr)[-400:]
            return
        job["qc_findings"] = findings
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        job["stage"] = None if job["status"] in ("done", "failed") else job["stage"]
        job["ended_ts"] = time.time()


def render_status(pid: str) -> dict:
    jobs = sorted((j for j in _JOBS.values() if j["project_id"] == pid),
                  key=lambda j: j["created_ts"], reverse=True)
    return {"project_id": pid, "job": (jobs[0] if jobs else None)}


def qc_report(pid: str) -> dict:
    p = PROJECTS_DIR / pid / "artifacts" / "render_qc_report.json"
    if not p.is_file():
        return {"project_id": pid, "report": None}
    rep = json.loads(p.read_text(encoding="utf-8"))
    # media-relative path for the render so findings can deep-link the player
    try:
        rep["render_media"] = Path(rep["render"]).resolve().relative_to(
            (PROJECTS_DIR / pid).resolve()).as_posix()
    except (KeyError, ValueError):
        rep["render_media"] = None
    return {"project_id": pid, "report": rep}
