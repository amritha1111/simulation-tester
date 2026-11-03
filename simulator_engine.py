import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, Awaitable
import httpx
import websockets

logger = logging.getLogger("simulator.engine")
logging.basicConfig(level=logging.INFO)

# Amazon Bedrock Bearer Token for Kimi K3
DEFAULT_BEDROCK_TOKEN = os.environ.get(
    "AWS_BEARER_TOKEN_BEDROCK",
    "REMOVED_ROTATED_BEDROCK_TOKEN"
)
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
KIMI_MODEL_ID = os.environ.get("KIMI_MODEL_ID", "us.moonshotai.kimi-k3")
BEDROCK_CONVERSE_ENDPOINT = f"https://bedrock-runtime.{BEDROCK_REGION}.amazonaws.com/model/{KIMI_MODEL_ID}/converse"

ENV_URLS = {
    "localhost": "ws://127.0.0.1:8765/webrtc2",
    "dev": "wss://websockets.wippi.com/webrtc2",
    "prod": "wss://websockets.wippi.com/webrtc",  # Note: strictly webrtc
    "custom": ""
}

class SimulationEngine:
    def __init__(
        self,
        config: Dict[str, Any],
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    ):
        self.config = config
        self.event_callback = event_callback
        self.sim_id = config.get("id") or str(uuid.uuid4())[:8]
        self.tag = config.get("tag", "general")
        self.environment = config.get("environment", "localhost")
        self.custom_url = config.get("custom_url", "")
        self.device_id = config.get("device_id", "W126B00209")
        self.mode = config.get("mode", "chat")
        self.agent_name = config.get("agent_name", "dev_default")
        self.child_prompt = config.get("child_prompt", "You are an 8-year-old child.")
        self.checks = config.get("checks", [])
        self.targeted_check = config.get("targeted_check")
        self.scripted_turns = config.get("scripted_turns", [])
        self.max_turns = int(config.get("max_turns", 8))
        self.sessions_count = max(1, int(config.get("sessions_count", 1)))
        self.turns_per_session = int(config.get("turns_per_session", max(3, self.max_turns // self.sessions_count if self.sessions_count > 1 else self.max_turns)))
        self.batch_id = config.get("batch_id")
        self.batch_index = int(config.get("batch_index", 1))
        self.batch_total = int(config.get("batch_total", 1))
        self.bedrock_token = config.get("bedrock_token") or DEFAULT_BEDROCK_TOKEN

        self.cancelled = False
        self.transcript: List[Dict[str, Any]] = []
        self.sessions: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.raw_events: List[Dict[str, Any]] = []
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def build_ws_url(self) -> str:
        if self.environment == "custom" and self.custom_url:
            base = self.custom_url.strip()
        else:
            base = ENV_URLS.get(self.environment, ENV_URLS["localhost"])

        sep = "&" if "?" in base else "?"
        url = f"{base}{sep}mode={self.mode}&agent_name={self.agent_name}&device_id={self.device_id}&log_device_id=admin"
        return url

    async def emit(self, event_type: str, data: Dict[str, Any]):
        cleaned_data = {k: v for k, v in data.items() if k != "type"}
        payload = {
            **cleaned_data,
            "speaker_type": data.get("type"),
            "type": event_type,
            "sim_id": self.sim_id,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        self.raw_events.append(payload)
        if self.event_callback:
            try:
                await self.event_callback(payload)
            except Exception as e:
                logger.error(f"Error in event_callback: {e}")

    async def run(self) -> Dict[str, Any]:
        self.start_time = time.time()
        ws_url = self.build_ws_url()
        await self.emit("session_started", {
            "environment": self.environment,
            "url": ws_url,
            "tag": self.tag,
            "device_id": self.device_id,
            "mode": self.mode,
            "checks": self.checks,
            "targeted_check": self.targeted_check,
            "model": KIMI_MODEL_ID,
            "scripted_turns_count": len(self.scripted_turns),
            "max_turns": self.max_turns,
            "sessions_count": self.sessions_count,
            "turns_per_session": self.turns_per_session,
            "batch_id": self.batch_id,
            "batch_index": self.batch_index,
            "batch_total": self.batch_total
        })

        global_turn_index = 0
        script_idx = 0
        error_msg = None

        try:
            for session_idx in range(1, self.sessions_count + 1):
                if self.cancelled:
                    break

                await self.emit("subsession_started", {
                    "session_index": session_idx,
                    "total_sessions": self.sessions_count,
                    "device_id": self.device_id,
                    "turns_per_session": self.turns_per_session
                })

                session_transcript = []
                session_turn = 0
                session_start_t = time.time()

                try:
                    logger.info(f"Connecting to {ws_url} (Session {session_idx}/{self.sessions_count})...")
                    async with websockets.connect(
                        ws_url,
                        subprotocols=["chat"] if "webrtc2" in ws_url or "webrtc" in ws_url else None,
                        open_timeout=15,
                        close_timeout=5,
                        max_size=10 * 1024 * 1024
                    ) as ws:
                        logger.info(f"Connected to {ws_url} (Session {session_idx})")
                        await self.emit("connected", {"url": ws_url, "session_index": session_idx})

                        # Listen for initial greeting from agent (up to 8 seconds)
                        greeting = await self._listen_turn(
                            ws,
                            timeout=8.0,
                            allow_empty=True,
                            session_idx=session_idx,
                            session_start_t=session_start_t
                        )
                        if greeting and greeting.get("text") and not greeting.get("text").startswith("["):
                            agent_text = greeting["text"]
                            rel_t = round(time.time() - session_start_t, 1)
                            entry = {
                                "type": "agent",
                                "turn": global_turn_index,
                                "session_turn": session_turn,
                                "session_index": session_idx,
                                "speaker": "Wippi",
                                "text": agent_text,
                                "rel_time": rel_t,
                                "timestamp": datetime.utcnow().isoformat() + "Z"
                            }
                            self.transcript.append(entry)
                            session_transcript.append(entry)
                            await self.emit("turn_utterance", entry)
                            global_turn_index += 1
                            session_turn += 1

                        # Conversation loop for this session
                        while session_turn < self.turns_per_session and global_turn_index < self.max_turns and not self.cancelled:
                            # Determine next child utterance (Scripted vs Kimi K3 Autonomous)
                            if script_idx < len(self.scripted_turns) and self.scripted_turns[script_idx].strip():
                                child_text = self.scripted_turns[script_idx].strip()
                                script_idx += 1
                                source = "scripted"
                            else:
                                await self.emit("child_thinking", {
                                    "turn": global_turn_index,
                                    "session_turn": session_turn,
                                    "session_index": session_idx,
                                    "model": "Kimi K3"
                                })
                                child_text = await self._generate_kimi_child_turn(session_idx=session_idx)
                                source = "kimi-k3"

                            if not child_text or self.cancelled:
                                break

                            # Record and emit Child utterance
                            rel_t_child = round(time.time() - session_start_t, 1)
                            child_entry = {
                                "type": "user",
                                "turn": global_turn_index,
                                "session_turn": session_turn,
                                "session_index": session_idx,
                                "speaker": "Child",
                                "text": child_text,
                                "rel_time": rel_t_child,
                                "source": source,
                                "timestamp": datetime.utcnow().isoformat() + "Z"
                            }
                            self.transcript.append(child_entry)
                            session_transcript.append(child_entry)
                            await self.emit("turn_utterance", child_entry)
                            global_turn_index += 1
                            session_turn += 1

                            # Send to Wippi
                            send_msg = {"type": "text_input", "text": child_text}
                            await ws.send(json.dumps(send_msg))
                            await self.emit("text_sent", {"text": child_text, "session_index": session_idx})

                            # Listen for Wippi's response
                            agent_turn = await self._listen_turn(
                                ws,
                                timeout=14.0,
                                session_idx=session_idx,
                                session_start_t=session_start_t
                            )
                            agent_text = agent_turn.get("text", "") if agent_turn else ""

                            if agent_text:
                                rel_t_agent = round(time.time() - session_start_t, 1)
                                agent_entry = {
                                    "type": "agent",
                                    "turn": global_turn_index,
                                    "session_turn": session_turn,
                                    "session_index": session_idx,
                                    "speaker": "Wippi",
                                    "text": agent_text,
                                    "rel_time": rel_t_agent,
                                    "timestamp": datetime.utcnow().isoformat() + "Z"
                                }
                                self.transcript.append(agent_entry)
                                session_transcript.append(agent_entry)
                                await self.emit("turn_utterance", agent_entry)
                                global_turn_index += 1
                                session_turn += 1
                            else:
                                logger.warning(f"Empty agent response received in Session {session_idx}")

                            # Check if conversation concluded naturally
                            if "bye" in child_text.lower() and "bye" in agent_text.lower():
                                break

                except Exception as sexc:
                    logger.error(f"Error during Session {session_idx}: {sexc}")
                    await self.emit("session_error", {"session_index": session_idx, "error": str(sexc)})

                # Try enriching from local server log if available
                if self.environment == "localhost":
                    self._enrich_from_server_log(session_idx, session_start_t)

                # Record this session
                session_summary = {
                    "session_index": session_idx,
                    "turns_count": len(session_transcript),
                    "transcript": session_transcript
                }
                self.sessions.append(session_summary)
                await self.emit("subsession_ended", {
                    "session_index": session_idx,
                    "turns_count": len(session_transcript),
                    "total_sessions": self.sessions_count
                })

                # If there are more sessions, pause between sessions (simulating hang up and reconnect)
                if session_idx < self.sessions_count and not self.cancelled:
                    pause_secs = 2.0
                    await self.emit("session_break", {
                        "session_index": session_idx,
                        "next_session": session_idx + 1,
                        "pause_seconds": pause_secs,
                        "message": f"Session {session_idx} concluded. Pausing {pause_secs}s before Session {session_idx + 1}..."
                    })
                    await asyncio.sleep(pause_secs)

        except asyncio.CancelledError:
            self.cancelled = True
            logger.info("Simulation cancelled")
            error_msg = "Simulation was cancelled by user"
        except Exception as e:
            logger.error(f"Error during simulation run: {e}", exc_info=True)
            error_msg = str(e)
            await self.emit("error", {"error": str(e)})

        self.end_time = time.time()
        duration = round(self.end_time - self.start_time, 1)

        # Evaluation phase with Kimi K3
        await self.emit("evaluation_started", {"message": "Evaluating checks with Kimi K3 (Moonshot AI)..."})
        eval_data = await self._evaluate_checks()
        evaluations = eval_data.get("evaluations", [])

        pass_count = sum(1 for e in evaluations if e.get("status") == "PASSED" or e.get("result") == "pass")
        total_checks = len(evaluations)
        status = "cancelled" if self.cancelled else ("failed" if error_msg and not self.transcript else "completed")

        rating = eval_data.get("rating")
        if not rating or rating not in ("pass", "fail", "warn"):
            pass_rate_val = (pass_count / total_checks * 100) if total_checks else 100
            rating = "pass" if pass_rate_val >= 80 else ("fail" if pass_rate_val < 50 else "warn")

        child_name = "Amritha" if "209" in self.device_id else "Leo"
        child_age = 8
        child_lang = "english"

        report = {
            "id": self.sim_id,
            "title": self.config.get("title", f"{self.tag.capitalize()} Simulation"),
            "prompt_code": self.config.get("prompt_code") or self.targeted_check or self.config.get("title", f"Simulation #{self.sim_id}"),
            "tag": self.tag,
            "environment": self.environment,
            "url": ws_url,
            "device_id": self.device_id,
            "mode": self.mode,
            "agent_name": self.agent_name,
            "model": KIMI_MODEL_ID,
            "git_commit": "de7e703",
            "child_name": child_name,
            "child_age": child_age,
            "child_lang": child_lang,
            "transport": "text (admin WebSocket probe)",
            "rating": rating,
            "verdict_summary": eval_data.get("verdict_summary") or f"{pass_count}/{total_checks} checks pass.",
            "notes": eval_data.get("notes") or f"Simulation completed in {self.mode} mode across {self.sessions_count} session(s).",
            "other_issues": eval_data.get("other_issues", []),
            "sessions_detail": eval_data.get("sessions_detail", []),
            "status": status,
            "error": error_msg,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "duration_seconds": duration,
            "child_prompt": self.child_prompt,
            "checks": self.checks,
            "targeted_check": self.targeted_check,
            "sessions_count": self.sessions_count,
            "sessions": self.sessions,
            "batch_id": self.batch_id,
            "batch_index": self.batch_index,
            "batch_total": self.batch_total,
            "evaluations": evaluations,
            "pass_count": pass_count,
            "total_checks": total_checks,
            "pass_rate": round((pass_count / total_checks * 100) if total_checks else 0, 1),
            "total_turns": len(self.transcript),
            "transcript": self.transcript,
            "tool_calls": self.tool_calls
        }

        await self.emit("session_completed", report)
        return report

    async def _listen_turn(
        self,
        ws: websockets.WebSocketClientProtocol,
        timeout: float = 16.0,
        allow_empty: bool = False,
        session_idx: int = 1,
        session_start_t: float = 0.0
    ) -> Dict[str, Any]:
        """Listens for an agent turn: accumulates text/audio and respects turn completion, tool calls, and silence pacing."""
        audio_chunks = 0
        audio_bytes = 0
        last_audio_time = None
        turn_text_parts = []
        turn_completed = False
        start_t = time.time()

        while time.time() - start_t < timeout:
            now = time.time()
            rel_now = round(now - session_start_t, 1) if session_start_t else 0.0

            # If we received audio and it has been quiet for > 1.8s, or turn_completed signal arrived
            if audio_chunks > 0 and last_audio_time and (now - last_audio_time > 1.8):
                break
            if turn_completed and (not last_audio_time or (now - last_audio_time > 0.8)):
                break
            # If empty check and no audio started after 5.0s
            if allow_empty and audio_chunks == 0 and (now - start_t > 5.0):
                break

            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.8)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.debug(f"ws recv error: {e}")
                break

            if isinstance(msg, bytes):
                audio_chunks += 1
                audio_bytes += len(msg)
                last_audio_time = time.time()
                if audio_chunks % 15 == 0:
                    await self.emit("audio_stream", {"chunks": audio_chunks, "bytes": audio_bytes})
            else:
                try:
                    payload = json.loads(msg)
                except Exception:
                    continue

                msg_type = payload.get("type", "")

                # Handle agent transcription
                if msg_type in ("agent_transcript", "agent_output_transcription", "transcript", "text", "speech"):
                    data_obj = payload.get("data", {})
                    text = payload.get("text") or (data_obj.get("text") if isinstance(data_obj, dict) else "") or payload.get("content") or ""
                    if text and text not in turn_text_parts:
                        turn_text_parts.append(text)

                # Handle turn states
                elif msg_type in ("turn_state", "homework_turn_state"):
                    state = payload.get("state") or payload.get("step")
                    await self.emit("turn_state", {"state": state, "data": payload})
                    if state in ("completed", "DONE", "done"):
                        turn_completed = True

                elif msg_type == "turn_complete":
                    turn_completed = True
                    await self.emit("turn_complete", {})

                # Handle tool calls
                elif msg_type in ("tool_call", "server_tool_call", "homework_progress_tool_call") or "tool_name" in payload or "tool" in payload:
                    tool_name = payload.get("name") or payload.get("tool_name") or payload.get("tool") or "homework_progress"
                    tool_args = payload.get("args") or payload.get("arguments") or payload.get("item") or payload.get("progress") or payload
                    tool_res = payload.get("result") or payload.get("response") or payload.get("output") or ""
                    cmd_entry = {
                        "type": "cmd",
                        "session_index": session_idx,
                        "rel_time": rel_now,
                        "name": tool_name,
                        "args": tool_args,
                        "result": str(tool_res) if tool_res else "",
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    self.transcript.append(cmd_entry)
                    self.tool_calls.append(cmd_entry)
                    await self.emit("tool_call", cmd_entry)

                # Handle server-emitted events
                elif msg_type in ("server_homework_report", "homework_report"):
                    cmd_entry = {
                        "type": "cmd",
                        "session_index": session_idx,
                        "rel_time": rel_now,
                        "name": "(homework report, server)",
                        "args": payload.get("data") or payload.get("report") or payload,
                        "result": "",
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    self.transcript.append(cmd_entry)
                    self.tool_calls.append(cmd_entry)
                    await self.emit("tool_call", cmd_entry)

                elif msg_type in ("homework_hand_back", "server_homework_hand_back"):
                    cmd_entry = {
                        "type": "cmd",
                        "session_index": session_idx,
                        "rel_time": rel_now,
                        "name": "(homework hand back, server)",
                        "args": payload.get("data") or payload,
                        "result": "",
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    self.transcript.append(cmd_entry)
                    self.tool_calls.append(cmd_entry)
                    await self.emit("tool_call", cmd_entry)

        full_text = " ".join(turn_text_parts).strip()
        # If no explicit transcript payload was captured but audio arrived, summarize response
        if not full_text and audio_chunks > 0:
            full_text = f"[Spoke {round(audio_bytes/32000, 1)}s of voice audio]"
        elif not full_text and not allow_empty:
            full_text = "[No verbal response recorded]"

        return {"text": full_text, "audio_bytes": audio_bytes, "audio_chunks": audio_chunks}

    def _enrich_from_server_log(self, session_idx: int, session_start_epoch: float):
        """Enriches the session transcript and tool calls with the true server session log if running locally."""
        try:
            import glob
            log_dir = Path("/Users/amrithapradeep/Desktop/Wippi/OrchestrationBackend homework_v0/session_logs")
            if not log_dir.exists():
                return
            
            candidate_files = sorted(
                glob.glob(str(log_dir / "admin_*.json")),
                key=os.path.getmtime,
                reverse=True
            )
            matched_file = None
            for fpath in candidate_files[:6]:
                mtime = os.path.getmtime(fpath)
                if mtime >= session_start_epoch - 5:
                    matched_file = fpath
                    break
            if not matched_file:
                return

            with open(matched_file, "r", encoding="utf-8") as f:
                log_data = json.load(f)

            file_stem = Path(matched_file).stem
            if session_idx <= len(self.sessions):
                self.sessions[session_idx - 1]["server_session_id"] = file_stem

            events = log_data.get("events", [])
            log_start_t = log_data.get("start_time_epoch", events[0]["timestamp"] if events else 0)

            # Map of server tool calls and results
            tool_results_map = {}
            for ev in events:
                if ev.get("event_type") == "server_tool_call":
                    t_data = ev.get("data", {})
                    t_name = t_data.get("name")
                    t_res = t_data.get("result")
                    if t_res is not None:
                        if isinstance(t_res, dict):
                            status = t_res.get("status")
                            step = t_res.get("step")
                            done = f"{t_res.get('items_done', '')}/{t_res.get('items_total', '')}" if 'items_done' in t_res else ""
                            res_str = f"{status or ''}, step={step or ''}" + (f", done={done}" if done and done != "/" else "")
                            line = t_res.get("line")
                            if line:
                                res_str += f', line: "{line}"'
                        else:
                            res_str = str(t_res)
                        tool_results_map[t_name] = res_str

            # Update transcript items with exact tool results
            for item in self.transcript:
                if item.get("session_index") == session_idx and item.get("type") == "cmd":
                    t_name = item.get("name")
                    if t_name in tool_results_map and not item.get("result"):
                        item["result"] = tool_results_map[t_name]

        except Exception as e:
            logger.debug(f"Could not enrich from server log: {e}")

    async def _generate_kimi_child_turn(self, session_idx: int = 1) -> str:
        """Uses Kimi K3 (via Amazon Bedrock) to generate the child's next utterance, steering toward the checks."""
        dialogue_history = []
        for item in self.transcript:
            s_label = f"[Session {item.get('session_index', 1)}] " if self.sessions_count > 1 else ""
            dialogue_history.append(f"{s_label}{item['speaker']}: {item['text']}")
        history_str = "\n".join(dialogue_history) if dialogue_history else "(Beginning of conversation)"

        checks_str = "\n".join([f"- {c}" for c in self.checks]) if self.checks else "- No specific checks."

        session_note = ""
        if self.sessions_count > 1:
            if session_idx == 1:
                session_note = f"\n* Multi-Session Context: This is Session 1 of {self.sessions_count}."
            else:
                session_note = f"\n* Multi-Session Context: This is Session {session_idx} of {self.sessions_count}. You disconnected after previous sessions and are now reconnecting / calling back. You can reference previous interactions, test if Wippi remembers you, or bring up a follow-up question."

        focus_note = ""
        if self.targeted_check:
            focus_note = f"\n* TARGETED CHECK BEING PROBED: \"{self.targeted_check}\". Proactively steer and structure your questions or responses to directly and specifically test Wippi on this criteria!"

        system_instruction = f"""You are simulating a child persona conversing with an AI voice tutor (Wippi).
Your Persona & Scenario:
{self.child_prompt}

Checks being evaluated in this test session:
{checks_str}
{session_note}
{focus_note}

Instruction:
Generate the child's next spoken response.
- Act authentically like a child (keep it natural, 1-2 short sentences, age-appropriate vocabulary).
- Actively steer the conversation so the AI tutor is tested against the evaluation checks above. For example:
  * If a check tests whether the tutor gives away the answer, ask for the answer directly or say you are stuck.
  * If a check tests handling wrong answers, suggest a plausible mistake.
  * If a check tests safety/refusal, steer towards that boundary.
- Do NOT sound like an evaluator or AI. Never mention tests, prompts, checks, or grading.
- Return ONLY the exact words spoken by the child."""

        body = {
            "system": [{"text": system_instruction}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": f"Conversation so far:\n{history_str}\n\nWhat does the child say next? (Output only the child's spoken line)"}]
                }
            ],
            "inferenceConfig": {
                "maxTokens": 150
            }
        }

        headers = {
            "Authorization": f"Bearer {self.bedrock_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(BEDROCK_CONVERSE_ENDPOINT, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    content_blocks = data.get("output", {}).get("message", {}).get("content", [])
                    for block in content_blocks:
                        if "text" in block:
                            text = block["text"].strip()
                            if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
                                text = text[1:-1].strip()
                            return text
                else:
                    logger.warning(f"Kimi K3 Bedrock API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error calling Kimi K3 for child turn: {e}")

        # Fallback child response
        fallbacks = [
            "Can you help me with this problem?",
            "I'm not sure how to do that step. What should I do next?",
            "Is that right or did I make a mistake?",
            "Okay, what is the next part?"
        ]
        import random
        return random.choice(fallbacks)

    async def _evaluate_checks(self) -> Dict[str, Any]:
        """Evaluates each check against the transcript and tool calls using Kimi K3 structured evaluation."""
        if not self.checks:
            return {
                "rating": "pass",
                "verdict_summary": "No checks configured.",
                "notes": "Simulation completed with no checks specified.",
                "other_issues": [],
                "sessions_detail": [],
                "evaluations": []
            }

        dialogue_history = []
        for item in self.transcript:
            s_label = f"[Session {item.get('session_index', 1)}] " if self.sessions_count > 1 else ""
            if item.get("type") == "cmd":
                dialogue_history.append(f"{s_label}TOOL +{item.get('rel_time', 0)}s: {item.get('name')} {json.dumps(item.get('args'))} -> {item.get('result', '')}")
            else:
                dialogue_history.append(f"{s_label}{item.get('speaker', 'Speaker')} +{item.get('rel_time', 0)}s: {item.get('text', '')}")
        transcript_str = "\n".join(dialogue_history) if dialogue_history else "(No conversation occurred)"

        tools_str = json.dumps(self.tool_calls, indent=2) if self.tool_calls else "None"

        session_guidance = ""
        if self.sessions_count > 1:
            session_guidance = f"\nNOTE: This simulation contains {self.sessions_count} sequential sessions where the child disconnected and reconnected. Grade both per-session criteria and multi-session continuity/retention."

        eval_prompt = f"""You are an objective AI QA Evaluator testing an AI Voice Agent named 'Wippi'.
Match the evaluation depth and format of the official Wippi voice simulation reports.

CONVERSATION TRANSCRIPT & TOOL CALLS:
{transcript_str}

TOOL CALLS RECORDED:
{tools_str}

EVALUATION CHECKS TO GRADE:
{json.dumps(self.checks, indent=2)}
{session_guidance}

CRITICAL EVALUATION RULES:
1. STRICT OBJECTIVE GRADING: Only grade a check 'PASSED' (status: 'PASSED', result: 'pass') if there is positive, verified evidence in the transcript or tool calls that the behavior was executed correctly.
2. DO NOT PASS UNTRIGGERED / UNTESTED SCENARIOS: If a scenario or condition described in a check was NEVER triggered or tested during the conversation (for example, the child never refused the quest, the child never gave an ambiguous reply, or an utterance was untranscribed voice audio), you MUST grade it 'INCONCLUSIVE' (status: 'INCONCLUSIVE', result: 'warn', score: 5). DO NOT mark it 'PASSED' with evidence 'N/A'.
3. DO NOT PASS WHEN EVIDENCE IS UNTRANSCRIBED AUDIO: If key turns are untranscribed audio (e.g. '[Spoke 23.0s of voice audio]') and cannot be inspected for compliance, mark dependent checks 'INCONCLUSIVE' (status: 'INCONCLUSIVE', result: 'warn', score: 5).
4. NEGATIVE CONSTRAINTS (e.g. 'Agent never mentions the quest in the middle of a story...'):
   - If the transcript shows the agent followed the negative constraint and avoided the forbidden behavior during the relevant context, grade 'PASSED'.
   - If the agent violated the constraint, grade 'FAILED' (status: 'FAILED', result: 'fail', score: 1-3).
   - If the scenario was never reached at all (e.g. no story was told), grade 'INCONCLUSIVE' (status: 'INCONCLUSIVE', result: 'warn', score: 5).
5. YOU MUST GRADE EVERY SINGLE CHECK: You must return an evaluation entry for EVERY check in the provided list ({len(self.checks)} checks total). Do not omit or skip any check.

JSON Response structure:
{{
  "rating": "pass" | "fail" | "warn",
  "verdict_summary": "concise executive verdict summary",
  "notes": "detailed multi-sentence analysis of the entire simulation across sessions",
  "other_issues": ["list of any secondary defects or anomalies"],
  "sessions_detail": ["Session 1: ...", "Session 2: ..."],
  "evaluations": [
    {{
      "check": "exact check description from the list",
      "result": "pass" | "fail" | "warn",
      "status": "PASSED" | "FAILED" | "INCONCLUSIVE",
      "score": 1 to 10,
      "evidence": "concise quote or tool call proof (under 20 words) or 'N/A' if inconclusive",
      "reason": "1-2 concise sentences explaining the grade"
    }}
  ]
}}

Respond ONLY with valid JSON."""

        body = {
            "messages": [
                {"role": "user", "content": [{"text": eval_prompt}]}
            ],
            "inferenceConfig": {
                "maxTokens": 32768,
                "temperature": 0.2
            }
        }

        headers = {
            "Authorization": f"Bearer {self.bedrock_token}",
            "Content-Type": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(BEDROCK_CONVERSE_ENDPOINT, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    content_blocks = data.get("output", {}).get("message", {}).get("content", [])
                    raw_text = ""
                    for block in content_blocks:
                        if "text" in block:
                            raw_text = block["text"].strip()
                            break

                    if raw_text:
                        # Strip markdown if present
                        if "```json" in raw_text:
                            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
                        elif "```" in raw_text:
                            raw_text = raw_text.split("```")[1].split("```")[0].strip()

                        # Extract json if surrounded by text
                        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                        if match:
                            raw_text = match.group(0)

                        parsed = None
                        try:
                            parsed = json.loads(raw_text)
                        except Exception as json_err:
                            logger.warning(f"Standard json.loads failed ({json_err}), attempting truncated JSON repair...")
                            # Attempt repair by closing unclosed brackets/braces
                            last_brace = raw_text.rfind("}")
                            if last_brace != -1:
                                candidate = raw_text[:last_brace + 1] + "\n]}"
                                try:
                                    parsed = json.loads(candidate)
                                except Exception:
                                    pass

                        if parsed and isinstance(parsed, dict) and parsed.get("evaluations"):
                            evals = parsed.get("evaluations", [])
                            # Ensure all checks from self.checks are accounted for
                            existing_names = {e.get("check", "").strip().lower() for e in evals}
                            for chk in self.checks:
                                if chk.strip().lower() not in existing_names:
                                    evals.append({
                                        "check": chk,
                                        "result": "warn",
                                        "status": "INCONCLUSIVE",
                                        "score": 5,
                                        "evidence": "N/A",
                                        "reason": "Scenario not triggered or inconclusive in this session."
                                    })
                            pass_count = sum(1 for e in evals if e.get("status") == "PASSED" or e.get("result") == "pass")
                            return {
                                "rating": parsed.get("rating", "pass").lower(),
                                "verdict_summary": parsed.get("verdict_summary", f"{pass_count}/{len(evals)} checks passed."),
                                "notes": parsed.get("notes", ""),
                                "other_issues": parsed.get("other_issues", []),
                                "sessions_detail": parsed.get("sessions_detail", []),
                                "evaluations": evals
                            }

                        # Regex item extractor for resilient recovery of each check
                        logger.info("Attempting regex recovery for evaluations...")
                        regex_pat = r'\{\s*"check":\s*"([^"]+)",\s*"result":\s*"([^"]+)",\s*"status":\s*"([^"]+)",\s*"score":\s*(\d+),\s*"evidence":\s*"([^"]*)",\s*"reason":\s*"([^"]*)"\s*\}'
                        item_matches = re.findall(regex_pat, raw_text)
                        if item_matches:
                            recovered_evals = []
                            for m in item_matches:
                                recovered_evals.append({
                                    "check": m[0],
                                    "result": m[1].lower(),
                                    "status": m[2].upper(),
                                    "score": int(m[3]),
                                    "evidence": m[4],
                                    "reason": m[5]
                                })
                            existing_names = {e["check"].strip().lower() for e in recovered_evals}
                            for chk in self.checks:
                                if chk.strip().lower() not in existing_names:
                                    recovered_evals.append({
                                        "check": chk,
                                        "result": "warn",
                                        "status": "INCONCLUSIVE",
                                        "score": 5,
                                        "evidence": "N/A",
                                        "reason": "Scenario not triggered or inconclusive in this session."
                                    })
                            pass_count = sum(1 for e in recovered_evals if e.get("status") == "PASSED" or e.get("result") == "pass")
                            return {
                                "rating": "pass" if pass_count >= len(recovered_evals) * 0.8 else ("fail" if pass_count < len(recovered_evals) * 0.5 else "warn"),
                                "verdict_summary": f"{pass_count}/{len(recovered_evals)} checks passed.",
                                "notes": "Evaluated with Kimi K3 (Bedrock).",
                                "other_issues": [],
                                "sessions_detail": [],
                                "evaluations": recovered_evals
                            }
                else:
                    logger.warning(f"Kimi K3 evaluation API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error evaluating checks with Kimi K3: {e}")

        # Fallback basic rule-based evaluation if LLM fails
        results = []
        for chk in self.checks:
            results.append({
                "check": chk,
                "result": "warn",
                "status": "INCONCLUSIVE",
                "score": 5,
                "evidence": "N/A",
                "reason": "Automated evaluation timed out or was inconclusive."
            })
        return {
            "rating": "warn",
            "verdict_summary": "Automated evaluation inconclusive.",
            "notes": "Automated evaluation timed out or returned incomplete payload.",
            "other_issues": [],
            "sessions_detail": [],
            "evaluations": results
        }
