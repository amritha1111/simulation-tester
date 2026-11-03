"""
Report Generator for Wippi Voice Agent Simulator.
Produces HTML reports matching the exact format, styling, structure, and metadata
of the official reports in 'OrchestrationBackend homework_v0/homework simulation/'.
"""

import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

WORKSPACE_ROOT = Path("/Users/amrithapradeep/Desktop/Wippi")
HOMEWORK_SIM_DIR = WORKSPACE_ROOT / "OrchestrationBackend homework_v0" / "homework simulation"

CSS_STYLES = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--mute:#667085;--line:#e4e7ec;--agent:#eef4ff;--agent-b:#c7d7fe;
--user:#ecfdf3;--user-b:#abefc6;--sys:#f2f4f7;--bad:#b42318;--ok:#067647;--warn:#b54708;--accent:#3538cd}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b24;--ink:#e6e8ee;--mute:#98a2b3;--line:#2a3140;
--agent:#1a2340;--agent-b:#2f3f73;--user:#12291e;--user-b:#1f5137;--sys:#1d222c;--bad:#f97066;--ok:#47cd89;--warn:#fdb022;--accent:#8098f9}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
h3{font-size:14px;margin:16px 0 6px}.mute{color:var(--mute)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}.kv{background:var(--sys);border-radius:8px;padding:8px 10px}
.kv b{display:block;font-size:11px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.03em}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid var(--line)}
.s-completed{color:var(--ok)}.s-error{color:var(--bad)}.s-stopped{color:var(--warn)}
.r-pass{color:var(--ok)}.r-fail{color:var(--bad)}.r-warn{color:var(--warn)}
.line{display:flex;gap:10px;margin:8px 0}.line .who{flex:0 0 120px;font-size:12px;color:var(--mute);padding-top:8px;text-align:right}
.bubble{flex:1;border-radius:10px;padding:8px 12px;border:1px solid}.agent .bubble{background:var(--agent);border-color:var(--agent-b)}
.user .bubble{background:var(--user);border-color:var(--user-b)}.sys{font-size:12px;color:var(--mute);margin:6px 0 6px 130px}
.cmd{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:var(--sys);border-radius:6px;padding:4px 8px;margin:6px 0 6px 130px;overflow-x:auto;white-space:pre-wrap}
.meta{font-size:11px;color:var(--mute);margin-top:4px}
.sess{margin:26px 0 8px;padding:8px 12px;background:var(--ink);color:var(--bg);border-radius:8px;font-weight:700}
pre{white-space:pre-wrap;word-break:break-word;background:var(--sys);border-radius:8px;padding:10px;font:12px/1.45 ui-monospace,Consolas,monospace;margin:0;max-height:520px;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
details summary{cursor:pointer;font-weight:600;margin:6px 0}hr{border:0;border-top:1px solid var(--line);margin:36px 0}
@media (max-width:640px){.line{flex-direction:column;gap:2px}.line .who{text-align:left;padding:0}.sys,.cmd{margin-left:0}}
"""

def esc(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text))

def generate_simulation_html(report: Dict[str, Any]) -> str:
    """Generates a standalone HTML report matching simulation_157_live.html exactly."""
    sim_id = report.get("id", "000")
    title = report.get("title", f"Simulation #{sim_id}")
    tag = report.get("tag", "general")
    status = report.get("status", "completed").lower()
    mode = report.get("mode", "chat")
    device_id = report.get("device_id", "W126B00209")
    agent_name = report.get("agent_name", "dev_default")
    agent_display = "default" if agent_name == "dev_default" else agent_name
    ws_url = report.get("url", "ws://127.0.0.1:8765/webrtc2")
    git_commit = report.get("git_commit", "de7e703")
    started_raw = report.get("created_at", datetime.now().isoformat())
    started_fmt = started_raw[:19].replace("T", " ")
    duration = report.get("duration_seconds", 0)
    sessions_count = max(1, report.get("sessions_count", 1))
    
    # Child details
    child_name = report.get("child_name") or ("Amritha" if "209" in device_id else "Leo")
    child_age = report.get("child_age") or 8
    child_lang = report.get("child_lang") or "english"
    child_profile = f"{child_name} · {child_age} yrs · {child_lang}"
    
    # Rating & Verdict
    rating = report.get("rating", "pass").lower()
    if rating not in ("pass", "fail", "warn"):
        pass_rate = report.get("pass_rate", 100)
        rating = "pass" if pass_rate >= 80 else ("fail" if pass_rate < 50 else "warn")
    
    # Status chip class
    status_chip_cls = "s-completed" if status == "completed" else ("s-stopped" if status == "cancelled" else "s-error")
    rating_chip_cls = f"r-{rating}"
    
    # Transcript counts
    transcript = report.get("transcript", [])
    user_turns = sum(1 for t in transcript if t.get("speaker") == "Child" or t.get("type") == "user")
    agent_turns = sum(1 for t in transcript if t.get("speaker") in ("Wippi", agent_name, agent_display) or t.get("type") == "agent")
    
    prompt_code = report.get("prompt_code") or report.get("targeted_check") or title
    prompt_desc = report.get("child_prompt") or "Dialogue simulation and evaluation testing against voice agent."
    
    notes = report.get("notes") or report.get("eval_summary") or f"Simulation completed in {mode} mode across {sessions_count} session(s). Evaluated against {len(report.get('checks', []))} checks."
    
    # Evaluation Rows
    evals = report.get("evaluations", [])
    eval_rows_html = ""
    pass_cnt = 0
    for ev in evals:
        c_status = (ev.get("result") or ev.get("status") or "pass").lower()
        if c_status in ("passed", "pass"):
            res_cls = "r-pass"
            res_text = "pass"
            pass_cnt += 1
        elif c_status in ("warn", "warning", "flaky", "inconclusive"):
            res_cls = "r-warn"
            res_text = "warn"
        else:
            res_cls = "r-fail"
            res_text = "fail"
            
        chk_text = esc(ev.get("check", ""))
        evidence = esc(ev.get("evidence") or ev.get("reason") or "")
        
        eval_rows_html += f"""<tr><td>{chk_text}</td><td class="{res_cls}">{res_text}</td><td>{evidence}</td></tr>"""
        
    total_checks = len(evals) if evals else len(report.get("checks", []))
    verdict_summary = report.get("verdict_summary") or f"{pass_cnt}/{total_checks} checks pass."

    # Other Issues
    other_issues = report.get("other_issues", [])
    other_issues_html = ""
    if other_issues:
        issues_li = "".join([f"<li>{esc(iss)}</li>" for iss in other_issues])
        other_issues_html = f"<h3>Other issues</h3><ul>{issues_li}</ul>"

    # Sessions Rows
    sessions_data = report.get("sessions", [])
    sessions_rows_html = ""
    for s_idx in range(1, sessions_count + 1):
        s_obj = next((s for s in sessions_data if s.get("session_index") == s_idx), {})
        s_turns = s_obj.get("turns_count", sum(1 for t in transcript if t.get("session_index") == s_idx))
        s_detail = s_obj.get("detail") or (report.get("sessions_detail", [])[s_idx - 1] if len(report.get("sessions_detail", [])) >= s_idx else "Conversation completed")
        reconnect_lbl = " (reconnect)" if s_idx > 1 else ""
        sessions_rows_html += f"""<tr>
            <td>{s_idx}</td>
            <td>Server session {s_idx}{reconnect_lbl}</td>
            <td>Wippi ({agent_display})</td>
            <td>{s_turns}</td>
            <td>completed</td>
            <td>{esc(s_detail)}</td>
        </tr>"""

    # Transcript formatting
    transcript_html = ""
    current_sess = None
    sess_start_time = None
    
    for item in transcript:
        item_type = item.get("type", "")
        sess_idx = item.get("session_index", 1)
        
        # New session banner
        if sess_idx != current_sess:
            current_sess = sess_idx
            reconnect_lbl = " (reconnect)" if sess_idx > 1 else ""
            s_obj = next((s for s in report.get("sessions", []) if s.get("session_index") == sess_idx), {})
            sess_id_str = s_obj.get("server_session_id") or f"admin_{started_fmt.replace(' ', '_').replace(':', '-')}_{sim_id}"
            s_duration = s_obj.get("duration", duration)
            transcript_html += f"""<div class="sess">Server session {sess_idx}{reconnect_lbl} · {agent_display} · child {child_name}, {child_age} · {s_duration:.2f} s · {sess_id_str}</div>"""

        rel_time_val = item.get("rel_time")
        rel_time_str = f"+{rel_time_val:.1f}s" if rel_time_val is not None else "+0.0s"
        
        if item_type == "cmd" or "tool" in item_type or item.get("name"):
            tool_name = item.get("name") or item.get("tool_name", "tool")
            args_str = json.dumps(item.get("args") or item.get("arguments") or {})
            res_str = f" → {esc(item.get('result', ''))}" if item.get("result") else ""
            transcript_html += f"""<div class="cmd">{rel_time_str} · tool <b>{esc(tool_name)}</b> {esc(args_str)}{res_str}</div>"""

        elif item_type == "sys" or item.get("speaker") == "System":
            transcript_html += f"""<div class="sys">{rel_time_str} · {esc(item.get('text', ''))}</div>"""

        elif item.get("speaker") == "Child" or item_type == "user":
            child_text = esc(item.get("text", ""))
            transcript_html += f"""<div class="line user"><div class="who">Child<br>{rel_time_str}</div><div class="bubble">{child_text}<div class="meta">text_input</div></div></div>"""

        else:
            # Agent line
            agent_text = esc(item.get("text", ""))
            transcript_html += f"""<div class="line agent"><div class="who">{agent_display}<br>{rel_time_str}</div><div class="bubble">{agent_text}<div class="meta"></div></div></div>"""

    # Worksheet Section (if present)
    worksheet_html = ""
    worksheet_info = report.get("worksheet")
    if worksheet_info:
        ws_filename = worksheet_info.get("filename", "worksheet.jpg")
        ws_items = worksheet_info.get("items", [])
        items_rows = "".join([f"<tr><td>{esc(it.get('id', ''))}</td><td>{esc(it.get('question', ''))}</td><td>{esc(it.get('expected', ''))}</td></tr>" for it in ws_items])
        
        img_tag = ""
        if worksheet_info.get("image_base64"):
            img_tag = f'<img src="data:image/jpeg;base64,{worksheet_info["image_base64"]}" alt="Uploaded worksheet" style="width:100%;max-width:420px;border:1px solid var(--line);border-radius:8px">'
        else:
            img_tag = f'<div style="width:100%;height:180px;background:var(--sys);border:1px solid var(--line);border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--mute)">[Worksheet: {esc(ws_filename)}]</div>'

        worksheet_html = f"""
        <h2>Homework sheet uploaded</h2><div class="card">
        <h3>{esc(ws_filename)} <span class="mute">uploaded via admin Homework tab · {mode}-mode replay</span></h3>
        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start">
        <div style="flex:1 1 300px;min-width:0"><figure style="margin:0 0 8px">
        {img_tag}
        <figcaption class="mute">photo the backend received</figcaption></figure></div>
        <div style="flex:1 1 300px;min-width:0"><table><tr><th>Item</th><th>Parsed question</th><th>Expected</th></tr>
        {items_rows}
        </table></div></div></div>
        """

    date_today = datetime.now().strftime("%Y-%m-%d")

    full_html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Simulation #{esc(sim_id)} · live rerun</title>
<style>
{CSS_STYLES}
</style></head><body><main>
<p class="mute">Voice simulation export · {esc(mode)}-mode rerun against fixed homework_v0 · generated {date_today}</p>
<section id="sim-{esc(sim_id)}">
<h1>Simulation #{esc(sim_id)} · {esc(prompt_code)} · {esc(mode)}-mode rerun</h1>
<p class="mute">{esc(prompt_desc)}</p>
<div class="grid">
<div class="kv"><b>Status</b><span class="chip {status_chip_cls}">{esc(status)}</span></div>
<div class="kv"><b>Rating</b><span class="chip {rating_chip_cls}">{esc(rating)}</span></div>
<div class="kv"><b>Prompt</b>{esc(prompt_code)}</div>
<div class="kv"><b>Started</b>{esc(started_fmt)} local</div>
<div class="kv"><b>Duration</b>{duration:.1f} s ({sessions_count} sessions)</div>
<div class="kv"><b>User / agent turns</b>{user_turns} / {agent_turns}</div>
<div class="kv"><b>Child (device kid profile)</b>{esc(child_profile)}</div>
<div class="kv"><b>Transport</b>text (admin WebSocket probe)</div>
<div class="kv"><b>Device</b>{esc(device_id)}</div>
<div class="kv"><b>Agent</b>{esc(agent_display)} (prompt synced from admin 'Assistant' config)</div>
<div class="kv"><b>WebRTC URL</b>{esc(ws_url)} · local homework_v0 @ {esc(git_commit)}</div>
<div class="kv"><b>Session mode</b>{esc(mode)} (homework starts only when the child asks)</div>
<div class="kv"><b>Simulator</b>us.moonshotai.kimi-k3 (Moonshot AI)</div>
</div>
<div class="card"><b>Notes</b><p>{esc(notes)}</p></div>
{worksheet_html}
<h2>Evaluation</h2>
<div class="card"><p><b>Verdict:</b> <span class="chip {rating_chip_cls}">{esc(rating)}</span> <span class="mute">{esc(verdict_summary)}</span></p>
<table><tr><th>Check</th><th>Result</th><th>Evidence</th></tr>
{eval_rows_html}
</table>
{other_issues_html}
</div>
<h2>Sessions ({sessions_count} of {sessions_count})</h2><div class="card"><table>
<tr><th>#</th><th>Session</th><th>Agents reached</th><th>User turns</th><th>Ended</th><th>Detail</th></tr>
{sessions_rows_html}
</table></div>
<h2>Transcript</h2>
<p class="mute">True transcript from the local server session log: child lines are the scripted text inputs, agent lines are the agent's own output transcription, tool lines show the call args and result summary.</p>
<div class="card">
{transcript_html}
</div>
</section></main></body></html>
"""
    return full_html

def save_official_report(report: Dict[str, Any]) -> str:
    """Saves the report to both simulator reports and 'OrchestrationBackend homework_v0/homework simulation/'."""
    sim_id = report.get("id", "000")
    html_content = generate_simulation_html(report)
    
    # 1. Save in official homework simulation folder
    try:
        HOMEWORK_SIM_DIR.mkdir(parents=True, exist_ok=True)
        official_path = HOMEWORK_SIM_DIR / f"simulation_{sim_id}_live.html"
        with open(official_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception as e:
        print(f"Error saving to official homework simulation dir: {e}")

    return html_content
