import asyncio
import csv
import io
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from storage import storage
from simulator_engine import SimulationEngine
from report_generator import generate_simulation_html, save_official_report

logger = logging.getLogger("simulator.app")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Wippi Voice Agent Simulator Dashboard", version="1.0.0")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory registry of active simulation engines and active WebSocket connections
active_engines: Dict[str, SimulationEngine] = {}
active_connections: Dict[str, Set[WebSocket]] = {}

class ConnectionManager:
    async def connect(self, sim_id: str, websocket: WebSocket):
        await websocket.accept()
        if sim_id not in active_connections:
            active_connections[sim_id] = set()
        active_connections[sim_id].add(websocket)

    def disconnect(self, sim_id: str, websocket: WebSocket):
        if sim_id in active_connections:
            active_connections[sim_id].discard(websocket)
            if not active_connections[sim_id]:
                del active_connections[sim_id]

    async def broadcast(self, sim_id: str, message: dict):
        if sim_id in active_connections:
            dead_sockets = set()
            for ws in active_connections[sim_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead_sockets.add(ws)
            for ws in dead_sockets:
                active_connections[sim_id].discard(ws)

manager = ConnectionManager()

# --- Web UI ---
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_file = TEMPLATES_DIR / "index.html"
    if html_file.exists():
        with open(html_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h3>Simulator Dashboard UI not found</h3>", status_code=404)

# --- Tags API ---
@app.get("/api/tags")
async def list_tags():
    return storage.get_tags()

@app.post("/api/tags")
async def save_tag(data: dict):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    saved = storage.save_tag(data)
    return saved

@app.delete("/api/tags/{name}")
async def delete_tag(name: str, delete_runs: bool = Query(False)):
    success = storage.delete_tag(name, delete_associated_runs=delete_runs)
    if not success:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"status": "deleted", "name": name}

# --- Prompts API ---
@app.get("/api/prompts")
async def list_prompts():
    return storage.get_prompts()

@app.post("/api/prompts")
async def save_prompt(data: dict):
    title = data.get("title", "").strip()
    prompt_text = data.get("prompt", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Prompt title is required")
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt content is required")
    saved = storage.save_prompt(data)
    return saved

@app.delete("/api/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    success = storage.delete_prompt(prompt_id)
    if not success:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "deleted", "id": prompt_id}

@app.delete("/api/simulations/{sim_id}")
async def delete_simulation(sim_id: str):
    success = storage.delete_simulation(sim_id)
    if not success:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return {"status": "deleted", "id": sim_id}

# --- Checks CSV API ---
def extract_checks_from_csv_text(text: str) -> List[str]:
    if text.startswith("\ufeff"):
        text = text[1:]
    f = io.StringIO(text)
    reader = csv.reader(f)
    header = next(reader, None)
    if not header:
        raise HTTPException(status_code=400, detail="CSV file is empty")
    
    checks_idx = -1
    for idx, col in enumerate(header):
        if col.strip().lower() == "checks":
            checks_idx = idx
            break
    if checks_idx == -1:
        for idx, col in enumerate(header):
            if col.strip().lower() == "check":
                checks_idx = idx
                break
                
    if checks_idx == -1:
        cleaned_headers = [c.strip() for c in header if c.strip()]
        raise HTTPException(
            status_code=400, 
            detail=f"No 'checks' column found in CSV. Found headers: {', '.join(cleaned_headers)}"
        )
        
    checks = []
    for row in reader:
        if len(row) > checks_idx:
            val = row[checks_idx].strip()
            if val:
                checks.append(val)
                
    return checks

@app.post("/api/checks/parse-csv")
async def parse_checks_csv_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .csv file")
    try:
        content = await file.read()
        text = content.decode("utf-8-sig", errors="replace")
        checks = extract_checks_from_csv_text(text)
        return {
            "checks": checks,
            "count": len(checks),
            "filename": file.filename
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse CSV: {str(e)}")

# --- Simulations API ---
@app.get("/api/simulations")
async def list_simulations(tag: Optional[str] = Query(None)):
    return storage.get_simulations(tag=tag)

@app.get("/api/simulations/{sim_id}")
async def get_simulation(sim_id: str):
    report = storage.get_simulation_report(sim_id)
    if not report:
        raise HTTPException(status_code=404, detail="Simulation report not found")
    return report

@app.get("/api/reports/{sim_id}/html", response_class=HTMLResponse)
async def get_simulation_html_report(sim_id: str):
    report = storage.get_simulation_report(sim_id)
    if not report:
        raise HTTPException(status_code=404, detail="Simulation report not found")
    html_content = generate_simulation_html(report)
    return HTMLResponse(content=html_content)

async def _run_engine_task(engine: SimulationEngine):
    sim_id = engine.sim_id
    try:
        report = await engine.run()
        storage.save_simulation_run(report)
    except Exception as e:
        logger.error(f"Engine run error for {sim_id}: {e}", exc_info=True)
        fail_report = {
            "id": sim_id,
            "status": "failed",
            "error": str(e),
            "tag": engine.tag,
            "environment": engine.environment,
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        storage.save_simulation_run(fail_report)
    finally:
        active_engines.pop(sim_id, None)

@app.post("/api/simulations/run")
async def start_simulation(config: dict, background_tasks: BackgroundTasks):
    sim_id = config.get("id") or str(uuid.uuid4())[:8]
    config["id"] = sim_id

    # Create event dispatcher
    async def event_handler(event: dict):
        await manager.broadcast(sim_id, event)
        # Also broadcast global run updates if finished
        if event.get("type") in ("session_completed", "error"):
            await manager.broadcast("global", event)

    engine = SimulationEngine(config, event_callback=event_handler)
    active_engines[sim_id] = engine

    # Record initial pending status
    initial_record = {
        "id": sim_id,
        "title": config.get("title", f"{config.get('tag', 'general').capitalize()} Run"),
        "tag": config.get("tag", "general"),
        "environment": config.get("environment", "localhost"),
        "status": "running",
        "pass_count": 0,
        "total_checks": len(config.get("checks", [])),
        "total_turns": 0,
        "sessions_count": config.get("sessions_count", 1),
        "targeted_check": config.get("targeted_check"),
        "batch_id": config.get("batch_id"),
        "batch_index": config.get("batch_index", 1),
        "batch_total": config.get("batch_total", 1),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "duration_seconds": 0
    }
    storage.save_simulation_run(initial_record)

    # Launch background task
    background_tasks.add_task(_run_engine_task, engine)

    return {"sim_id": sim_id, "status": "running"}

async def _run_batch_task(batch_id: str, sim_configs: List[dict]):
    total = len(sim_configs)
    await manager.broadcast("global", {
        "type": "batch_started",
        "batch_id": batch_id,
        "total_simulations": total,
        "first_sim_id": sim_configs[0]["id"] if sim_configs else None
    })

    for idx, config in enumerate(sim_configs):
        sim_id = config["id"]
        async def event_handler(event: dict):
            await manager.broadcast(sim_id, event)
            if event.get("type") in ("session_completed", "error"):
                await manager.broadcast("global", event)

        engine = SimulationEngine(config, event_callback=event_handler)
        active_engines[sim_id] = engine

        initial_record = {
            "id": sim_id,
            "title": config.get("title"),
            "tag": config.get("tag", "general"),
            "environment": config.get("environment", "localhost"),
            "status": "running",
            "pass_count": 0,
            "total_checks": len(config.get("checks", [])),
            "total_turns": 0,
            "sessions_count": config.get("sessions_count", 1),
            "targeted_check": config.get("targeted_check"),
            "batch_id": batch_id,
            "batch_index": idx + 1,
            "batch_total": total,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": 0
        }
        storage.save_simulation_run(initial_record)

        try:
            report = await engine.run()
            storage.save_simulation_run(report)
        except Exception as e:
            logger.error(f"Error in batch run {sim_id}: {e}", exc_info=True)
            fail_report = {
                "id": sim_id,
                "status": "failed",
                "error": str(e),
                "tag": engine.tag,
                "environment": engine.environment,
                "batch_id": batch_id,
                "batch_index": idx + 1,
                "batch_total": total,
                "created_at": datetime.utcnow().isoformat() + "Z"
            }
            storage.save_simulation_run(fail_report)
        finally:
            active_engines.pop(sim_id, None)

        # Broadcast progress
        await manager.broadcast("global", {
            "type": "batch_progress",
            "batch_id": batch_id,
            "completed_index": idx + 1,
            "total_simulations": total,
            "completed_sim_id": sim_id,
            "next_sim_id": sim_configs[idx + 1]["id"] if idx + 1 < total else None
        })

    # Broadcast completed
    analytics = storage.get_check_analytics(batch_id=batch_id)
    await manager.broadcast("global", {
        "type": "batch_completed",
        "batch_id": batch_id,
        "total_simulations": total,
        "analytics": analytics
    })

@app.post("/api/simulations/batch_run")
async def start_batch_simulation(payload: dict, background_tasks: BackgroundTasks):
    tag = payload.get("tag", "general")
    checks = payload.get("checks", [])
    simulations_per_check = max(1, int(payload.get("simulations_per_check", 1)))
    sessions_per_sim = max(1, int(payload.get("sessions_per_simulation", 1)))
    run_mode = payload.get("run_mode", "suite") # "per_check" or "suite"
    
    batch_id = f"batch_{str(uuid.uuid4())[:8]}"
    sim_configs: List[dict] = []

    if run_mode == "per_check" and checks:
        # Generate simulation runs for each check
        for chk_idx, chk in enumerate(checks):
            for rep in range(1, simulations_per_check + 1):
                s_id = str(uuid.uuid4())[:8]
                sim_configs.append({
                    **payload,
                    "id": s_id,
                    "title": f"Check {chk_idx+1} [Run {rep}/{simulations_per_check}]: {chk[:32]}",
                    "tag": tag,
                    "targeted_check": chk,
                    "checks": checks,
                    "sessions_count": sessions_per_sim,
                    "batch_id": batch_id
                })
    else:
        # Generate N simulation runs of the full suite
        for rep in range(1, simulations_per_check + 1):
            s_id = str(uuid.uuid4())[:8]
            sim_configs.append({
                **payload,
                "id": s_id,
                "title": f"{tag.capitalize()} Run {rep}/{simulations_per_check}",
                "tag": tag,
                "checks": checks,
                "sessions_count": sessions_per_sim,
                "batch_id": batch_id
            })

    # Update batch index / total
    total_sims = len(sim_configs)
    for i, cfg in enumerate(sim_configs):
        cfg["batch_index"] = i + 1
        cfg["batch_total"] = total_sims

    background_tasks.add_task(_run_batch_task, batch_id, sim_configs)

    return {
        "batch_id": batch_id,
        "total_simulations": total_sims,
        "first_sim_id": sim_configs[0]["id"] if sim_configs else None,
        "sim_ids": [c["id"] for c in sim_configs],
        "status": "running"
    }

@app.get("/api/analytics/checks")
async def get_check_analytics(tag: Optional[str] = Query(None), batch_id: Optional[str] = Query(None)):
    return storage.get_check_analytics(tag=tag, batch_id=batch_id)

@app.post("/api/simulations/{sim_id}/cancel")
async def cancel_simulation(sim_id: str):
    engine = active_engines.get(sim_id)
    if engine:
        engine.cancelled = True
        return {"status": "cancelling", "sim_id": sim_id}
    raise HTTPException(status_code=404, detail="No active simulation with that ID")

# --- Export Reports ---
@app.get("/api/reports/export")
async def export_reports(tag: Optional[str] = Query(None), format: str = Query("json")):
    sims = storage.get_simulations(tag=tag)
    detailed_reports = []
    for s in sims:
        rep = storage.get_simulation_report(s["id"])
        if rep:
            detailed_reports.append(rep)

    if format.lower() == "json":
        return JSONResponse(
            content=detailed_reports,
            headers={"Content-Disposition": f'attachment; filename="wippi_reports_{tag or "all"}.json"'}
        )

    # HTML Report Generator
    html_content = generate_html_export(detailed_reports, tag)
    return HTMLResponse(
        content=html_content,
        headers={"Content-Disposition": f'attachment; filename="wippi_reports_{tag or "all"}.html"'}
    )

def generate_html_export(reports: List[Dict[str, Any]], tag: Optional[str]) -> str:
    if not reports:
        return "<html><body><p>No simulation reports found for this tag.</p></body></html>"
    if len(reports) == 1:
        return generate_simulation_html(reports[0])
    
    # Multiple reports: render full official sections
    sections = [generate_simulation_html(r) for r in reports]
    combined = '<hr style="margin: 40px 0; border: 0; border-top: 2px solid #e4e7ec;">'.join(sections)
    return combined

# --- Live WebSocket Streaming ---
@app.websocket("/ws/live/{sim_id}")
async def live_simulation_stream(websocket: WebSocket, sim_id: str):
    await manager.connect(sim_id, websocket)
    try:
        # Replay buffered events if engine is active
        engine = active_engines.get(sim_id)
        if engine and engine.raw_events:
            for ev in list(engine.raw_events):
                try:
                    await websocket.send_json(ev)
                except Exception:
                    break
        elif not engine:
            # If simulation already completed before WS connected, deliver final report
            report = storage.get_simulation_report(sim_id)
            if report:
                await websocket.send_json({"type": "session_completed", **report})

        # Keep connection open and receive any ping/commands from client
        while True:
            data = await websocket.receive_text()
            # If client sends a cancellation command
            if data == "cancel":
                eng = active_engines.get(sim_id)
                if eng:
                    eng.cancelled = True
    except WebSocketDisconnect:
        manager.disconnect(sim_id, websocket)
    except Exception:
        manager.disconnect(sim_id, websocket)
