# Wippi Voice Agent Simulator Dashboard

A dedicated simulation and evaluation dashboard for testing Wippi Voice Agents against real-time WebSocket endpoints with **Moonshot AI Kimi K3** persona simulation and automated check evaluation via Amazon Bedrock.

---

## Features

1. **Dashboard UI**:
   - Modern, responsive dark-mode Single Page Application accessible at `http://localhost:8090`.
   - Real-time live transcript streaming, audio byte metrics, tool call monitoring, and check evaluation badges.

2. **Target Environment Selector**:
   - **Localhost**: `ws://127.0.0.1:8765/webrtc2`
   - **Dev WebSocket**: `wss://websockets.wippi.com/webrtc2`
   - **Prod WebSocket**: `wss://websockets.wippi.com/webrtc` (Prod uses standard `webrtc` without 2)
   - **Custom WebSocket**: Specify any custom endpoint with configurable `device_id` (default: `W126B00209`), `mode` (default: `chat`), and `agent_name`.

3. **Tag & Check Template System**:
   - Associate each simulation with a specific tag (e.g., `homework_v0`, `math_two_digit_carry`, `safety_and_refusal`).
   - Tags maintain default check templates that automatically pre-load when selected.
   - Filter, inspect, and retrieve historical simulation reports by tag.

4. **Child Agent (Powered by Moonshot AI Kimi K3)**:
   - **Scripted Opening Turns**: Send pre-defined turns to initialize scenarios deterministically.
   - **Autonomous Kimi K3 Continuation**: Kimi K3 (`us.moonshotai.kimi-k3` on Amazon Bedrock) dynamically generates authentic, age-appropriate child dialogue with internal chain-of-thought reasoning, steering the conversation to exercise and test the evaluation checks.

5. **Automated Check Evaluation**:
   - After the conversation completes, Kimi K3 evaluates the full dialogue transcript and recorded tool calls against each check.
   - Grades each check as `PASSED`, `FAILED`, or `INCONCLUSIVE` with detailed rationale and exact evidence quotes.

6. **Report Export**:
   - Export reports by tag in structured **JSON** format or standalone printable **HTML** reports.

---

## Quick Start

### 1. Launch the Dashboard
Run the start script from the workspace:
```bash
./simulator/run.sh
```
Or directly:
```bash
cd simulator
"../OrchestrationBackend homework_v0/.venv/bin/uvicorn" app:app --port 8090 --reload
```

### 2. Access the Web Interface
Open your browser to:
[http://localhost:8090](http://localhost:8090)

---

## Directory Structure

```
simulator/
├── app.py                # FastAPI web server, REST API & live WebSocket manager
├── simulator_engine.py   # WebSocket client, turn pacing, Kimi K3 child agent & evaluator
├── storage.py            # Tag templates and simulation run report store
├── run.sh                # Executable startup script (Bedrock Kimi K3 configured)
├── requirements.txt      # Dependency specification
├── templates/
│   └── index.html        # Single-page dashboard application
├── static/
│   ├── css/style.css     # Styling, chat bubbles, animations
│   └── js/app.js         # Reactive UI logic & WebSocket live streaming
└── storage/
    ├── tags.json         # Tag templates and check definitions
    ├── simulations.json  # Indexed history of simulation runs
    └── reports/          # Detailed JSON reports for each run
```
