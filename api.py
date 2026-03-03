"""
AVERROA — Account Intelligence Radar  |  Web API
=================================================
FastAPI backend providing:
  POST /api/run/company    — submit company-mode job
  POST /api/run/geography  — submit geography-mode job
  GET  /api/jobs/{job_id}  — poll job status + result
  GET  /api/stream/{job_id}— SSE real-time log stream
  GET  /api/download/{job_id}/json  — download JSON report
  GET  /api/download/{job_id}/md    — download Markdown report
  GET  /                   — serve frontend HTML

Run with:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import uuid
import json
import threading
import time
import queue
from typing import Any
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import pipeline functions from main.py
from main import (
    run_company_pipeline,
    discover_companies_in_geography,
    DEFAULT_OBJECTIVE,
    _check_env,
)

app = FastAPI(title="AVERROA Account Intelligence Radar", version="1.0.0")

# ─────────────────────────────────────────────
# JOB STORE
# ─────────────────────────────────────────────
class Job:
    def __init__(self, job_id: str):
        self.job_id    = job_id
        self.status    = "queued"   # queued | running | done | error
        self.logs: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None
        self._log_queue: queue.Queue = queue.Queue()
        self.companies: list[str] = []   # for geo mode index
        self.reports: list[dict] = []    # list of {company, json_path, md_path}

    def log(self, msg: str):
        self.logs.append(msg)
        self._log_queue.put(msg)

    def finish(self, result: dict):
        self.result = result
        self.status = "done"
        self._log_queue.put("__DONE__")

    def fail(self, err: str):
        self.error = err
        self.status = "error"
        self._log_queue.put("__DONE__")


_jobs: dict[str, Job] = {}


# ─────────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────────
class CompanyRequest(BaseModel):
    company_name: str
    objective: str = DEFAULT_OBJECTIVE


class GeoRequest(BaseModel):
    location: str
    sector: str
    top_n: int = 3
    objective: str = DEFAULT_OBJECTIVE


# ─────────────────────────────────────────────
# LOG CAPTURE — redirect print() into job logs
# ─────────────────────────────────────────────
import sys
import io

class JobLogger(io.TextIOBase):
    """Capture print() output and route it to job.log()."""
    def __init__(self, job: Job, original_stdout):
        self.job = job
        self.original = original_stdout
        self._buf = ""

    def write(self, s: str) -> int:
        self.original.write(s)   # still show in server terminal
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self.job.log(line)
        return len(s)

    def flush(self):
        self.original.flush()


# ─────────────────────────────────────────────
# BACKGROUND WORKERS
# ─────────────────────────────────────────────
def _run_company_job(job: Job, company_name: str, objective: str):
    orig = sys.stdout
    sys.stdout = JobLogger(job, orig)
    try:
        job.status = "running"
        report = run_company_pipeline(company_name, objective)
        if report:
            # Find saved files
            filename = "".join(
                c for c in company_name.strip().replace(" ", "_").lower()
                if c.isalnum() or c in "_-"
            )
            json_path = f"reports/{filename}.json"
            md_path   = f"reports/{filename}.md"
            job.reports.append({
                "company":   report.get("company_name", company_name),
                "json_path": json_path,
                "md_path":   md_path,
            })
            job.finish(report)
        else:
            job.fail("Pipeline returned no results. Check logs for details.")
    except Exception as e:
        job.fail(f"Unexpected error: {type(e).__name__}: {e}")
    finally:
        sys.stdout = orig


def _run_geo_job(job: Job, location: str, sector: str, top_n: int, objective: str):
    orig = sys.stdout
    sys.stdout = JobLogger(job, orig)
    try:
        job.status = "running"

        companies = discover_companies_in_geography(location, sector, top_n)
        if not companies:
            job.fail("No companies found for this location and sector.")
            return

        job.companies = companies
        summary = []

        for i, company in enumerate(companies, 1):
            print(f"\n[{i}/{len(companies)}] Processing: {company}")
            print("-" * 40)
            report = run_company_pipeline(company, objective)
            if report:
                filename = "".join(
                    c for c in company.strip().replace(" ", "_").lower()
                    if c.isalnum() or c in "_-"
                )
                json_path = f"reports/{filename}.json"
                md_path   = f"reports/{filename}.md"
                job.reports.append({
                    "company":   report.get("company_name", company),
                    "json_path": json_path,
                    "md_path":   md_path,
                })
                summary.append({
                    "company":     report.get("company_name", company),
                    "hq":          report.get("headquarters", "N/A"),
                    "bus_units":   len(report.get("business_units", [])),
                    "initiatives": len(report.get("strategic_initiatives", [])),
                    "executives":  len(report.get("executives", [])),
                })

        # Save geo index
        if summary:
            os.makedirs("reports", exist_ok=True)
            slug = f"{location}_{sector}".replace(" ", "_").replace(",", "").lower()[:30]
            idx_path = f"reports/geo_{slug}.md"
            with open(idx_path, "w", encoding="utf-8") as f:
                f.write(f"# Geography Intelligence Report\n\n")
                f.write(f"**Location:** {location}  \n**Sector:** {sector}\n\n")
                f.write(f"**Objective:** {objective}\n\n---\n\n## Companies Profiled\n\n")
                for s in summary:
                    f.write(f"### {s['company']}\n")
                    f.write(f"- **HQ:** {s['hq']}\n")
                    f.write(f"- Business Units: {s['bus_units']} found\n")
                    f.write(f"- Strategic Initiatives: {s['initiatives']} found\n")
                    f.write(f"- Executives: {s['executives']} found\n\n")

        job.finish({
            "location": location,
            "sector": sector,
            "companies_found": len(companies),
            "reports_generated": len(summary),
            "summary": summary,
        })

    except Exception as e:
        job.fail(f"Unexpected error: {type(e).__name__}: {e}")
    finally:
        sys.stdout = orig


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@app.post("/api/run/company")
async def run_company(req: CompanyRequest):
    if not req.company_name.strip():
        raise HTTPException(400, "company_name is required")
    job = Job(job_id := str(uuid.uuid4())[:8])
    _jobs[job_id] = job
    t = threading.Thread(
        target=_run_company_job,
        args=(job, req.company_name.strip(), req.objective),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.post("/api/run/geography")
async def run_geography(req: GeoRequest):
    if not req.location.strip() or not req.sector.strip():
        raise HTTPException(400, "location and sector are required")
    job = Job(job_id := str(uuid.uuid4())[:8])
    _jobs[job_id] = job
    t = threading.Thread(
        target=_run_geo_job,
        args=(job, req.location.strip(), req.sector.strip(), req.top_n, req.objective),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return {
        "job_id":   job.job_id,
        "status":   job.status,
        "logs":     job.logs,
        "result":   job.result,
        "error":    job.error,
        "reports":  job.reports,
    }


@app.get("/api/stream/{job_id}")
async def stream_logs(job_id: str):
    """Server-Sent Events stream for real-time log output."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")

    def event_generator():
        # Send buffered logs first
        for line in list(job.logs):
            yield f"data: {json.dumps({'log': line})}\n\n"

        if job.status in ("done", "error"):
            yield f"data: {json.dumps({'status': job.status, 'reports': job.reports})}\n\n"
            return

        # Then stream new lines as they arrive
        while True:
            try:
                msg = job._log_queue.get(timeout=30)
                if msg == "__DONE__":
                    yield f"data: {json.dumps({'status': job.status, 'reports': job.reports})}\n\n"
                    return
                yield f"data: {json.dumps({'log': msg})}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/download/{job_id}/json")
async def download_json(job_id: str, company_index: int = 0):
    job = _jobs.get(job_id)
    if not job or not job.reports:
        raise HTTPException(404, "No reports found for this job")
    if company_index >= len(job.reports):
        company_index = 0
    report = job.reports[company_index]
    path = report["json_path"]
    if not os.path.exists(path):
        raise HTTPException(404, f"Report file not found: {path}")
    safe_name = report["company"].replace(" ", "_").lower() + ".json"
    return FileResponse(path, filename=safe_name, media_type="application/json")


@app.get("/api/download/{job_id}/md")
async def download_md(job_id: str, company_index: int = 0):
    job = _jobs.get(job_id)
    if not job or not job.reports:
        raise HTTPException(404, "No reports found for this job")
    if company_index >= len(job.reports):
        company_index = 0
    report = job.reports[company_index]
    path = report["md_path"]
    if not os.path.exists(path):
        raise HTTPException(404, f"Report file not found: {path}")
    safe_name = report["company"].replace(" ", "_").lower() + ".md"
    return FileResponse(path, filename=safe_name, media_type="text/markdown")


@app.get("/api/health")
async def health():
    env = _check_env()
    return {"status": "ok", "env": env}