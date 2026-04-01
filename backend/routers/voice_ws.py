"""
Voice WebSocket endpoint — kept for reference, not registered in main.py.

Protocol (one connection per voice turn):
  C→S  JSON  {"type":"start","current_field":"...","language":"en"}
  S→C  JSON  {"type":"listening"}
  C→S  BYTES raw PCM audio (16-bit signed, 16kHz, mono) — stream until done speaking
  C→S  JSON  {"type":"end_turn"}
  S→C  BYTES raw PCM audio chunks from Gemini (24kHz) — play as they arrive
  S→C  JSON  {"type":"text_chunk","text":"..."}  — one or more, accumulate
  S→C  JSON  {"type":"turn_complete","reply":"...","field_updates":{...},"next_field":"..."}
  S→C  JSON  {"type":"error","message":"..."}  — on any failure
"""

import asyncio, json, os
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from dotenv import load_dotenv
from routers.assistant import sessions, _validated_field_updates
from services.reply_parser import parse_field_update_from_text, normalize_field_updates

load_dotenv()
router = APIRouter()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"


def _voice_prompt(session: dict, current_field: str, language: str) -> str:
    field_map = session["field_map"]
    f = field_map.get(current_field)
    if not f:
        return "You are a court form assistant. Help the user fill out their form."
    filled = sum(1 for v in session["answers"].values() if v is not None)
    total  = len(session["fields"])
    lang   = {"es": "Respond in Spanish.", "pt": "Respond in Portuguese."}.get(language, "Respond in English.")
    return f"""You are a voice assistant helping fill a Massachusetts court form.
RULES:
1. Only help fill this form. No legal advice.
2. Ask about ONE field: {f.label}.
3. On answer confirmed, output on its own line: FIELD_UPDATE: {{"{f.name}": "value"}}
4. Skip optional fields if user says skip/n/a: FIELD_UPDATE: {{"{f.name}": null}}
5. Auto-skip signature fields: FIELD_UPDATE: {{"{f.name}": null}}
6. Keep replies under 2 sentences. {lang}

FIELD: {f.name} | {f.label} | {"required" if f.required else "optional"}
INSTRUCTIONS: {f.instructions}
PROGRESS: {filled}/{total} filled."""


@router.websocket("/voice/ws/{session_id}")
async def voice_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()

    session = sessions.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    # ── Wait for start message ────────────────────────────────────────
    try:
        raw = await asyncio.wait_for(websocket.receive(), timeout=10.0)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close()
        return

    if "text" not in raw:
        await websocket.close()
        return

    msg = json.loads(raw["text"])
    if msg.get("type") != "start":
        await websocket.close()
        return

    current_field = msg.get("current_field", "")
    language      = msg.get("language", "en")
    system_prompt = _voice_prompt(session, current_field, language)
    field_map     = session["field_map"]
    fields        = session["fields"]

    await websocket.send_json({"type": "listening"})

    # ── Open Gemini Live session ──────────────────────────────────────
    try:
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_prompt,
        )

        async with client.aio.live.connect(model=LIVE_MODEL, config=cfg) as live:

            async def recv_from_client():
                """Forward browser PCM → Gemini Live; watch for end_turn."""
                try:
                    while True:
                        data = await websocket.receive()
                        if "bytes" in data:
                            await live.send_realtime_input(
                                audio=types.Blob(data=data["bytes"], mime_type="audio/pcm;rate=16000")
                            )
                        elif "text" in data:
                            ctrl = json.loads(data["text"])
                            if ctrl.get("type") == "end_turn":
                                await live.send_client_content(
                                    turns=types.Content(
                                        role="user",
                                        parts=[types.Part(text="done")]
                                    ),
                                    turn_complete=True
                                )
                                return
                except (WebSocketDisconnect, Exception):
                    pass

            async def send_to_client():
                """Forward Gemini Live audio/text → browser."""
                text_buf = ""
                try:
                    async with asyncio.timeout(30):
                        async for resp in live.receive():
                            # Audio — Gemini Live returns audio in parts[].inline_data
                            if resp.server_content and resp.server_content.model_turn:
                                for part in resp.server_content.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        try:
                                            await websocket.send_bytes(part.inline_data.data)
                                        except Exception:
                                            return
                                    if part.text:
                                        text_buf += part.text
                                        try:
                                            await websocket.send_json({
                                                "type": "text_chunk",
                                                "text": part.text
                                            })
                                        except Exception:
                                            return

                            # Turn complete → parse + send summary
                            if resp.server_content and resp.server_content.turn_complete:
                                reply, raw_upd = parse_field_update_from_text(text_buf)
                                upd = _validated_field_updates(
                                    normalize_field_updates(raw_upd), field_map, current_field
                                )
                                if upd:
                                    for k, v in upd.items():
                                        session["answers"][k] = v

                                next_f = next(
                                    (f.name for f in fields if f.name not in session["answers"]),
                                    None
                                )
                                req_fields = [f for f in fields if f.required]
                                req_filled = [
                                    f for f in req_fields
                                    if session["answers"].get(f.name) is not None
                                ]

                                try:
                                    await websocket.send_json({
                                        "type":          "turn_complete",
                                        "reply":         reply,
                                        "field_updates": upd,
                                        "next_field":    next_f,
                                        "progress": {
                                            "filled":          sum(
                                                1 for v in session["answers"].values()
                                                if v is not None
                                            ),
                                            "total":           len(fields),
                                            "required_filled": len(req_filled),
                                            "required_total":  len(req_fields),
                                        },
                                    })
                                except Exception:
                                    pass
                                text_buf = ""
                                return

                except asyncio.TimeoutError:
                    print("[VoiceWS] timed out waiting for Gemini response")
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": "No response from Gemini — please try again."
                        })
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[VoiceWS] send_to_client: {e}")

            await asyncio.gather(recv_from_client(), send_to_client())

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[VoiceWS] error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass