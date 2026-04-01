import os
import base64
import struct
import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Voice names to try in order — if one fails, try the next
_VOICE_NAMES = ["Kore", "Puck", "Aoede", "Charon"]


def _build_wav(raw_audio: bytes, sample_rate: int = 24000) -> bytes:
    num_channels, bits = 1, 16
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(raw_audio), b"WAVE",
        b"fmt ", 16, 1, num_channels, sample_rate,
        sample_rate * num_channels * (bits // 8),
        num_channels * (bits // 8), bits,
        b"data", len(raw_audio)
    ) + raw_audio


@router.post("/voice/transcribe")
async def transcribe(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    b64 = base64.b64encode(audio_bytes).decode()
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{
            "parts": [
                {"inline_data": {"mime_type": "audio/webm", "data": b64}},
                {"text": "Transcribe exactly what was said. Return only the transcribed text, nothing else."}
            ]
        }]
    )
    return {"transcript": response.text.strip()}


class SpeakRequest(BaseModel):
    text: str
    language: str = "en"


@router.post("/voice/speak")
async def speak(req: SpeakRequest):
    lang_instruction = {
        "es": "Speak in Spanish.",
        "pt": "Speak in Portuguese.",
        "en": "Speak in English.",
    }.get(req.language, "Speak in English.")

    text = req.text[:500]
    last_error = None

    # Try each voice with up to 2 retries each
    for voice_name in _VOICE_NAMES:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash-preview-tts",
                    contents=f"{lang_instruction} Say the following: {text}",
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voice_name
                                )
                            )
                        )
                    )
                )
                candidate = response.candidates[0] if response.candidates else None
                if (
                    candidate
                    and candidate.content
                    and candidate.content.parts
                    and candidate.content.parts[0].inline_data
                    and candidate.content.parts[0].inline_data.data
                ):
                    raw_audio = candidate.content.parts[0].inline_data.data
                    print(f"[TTS] Success — voice={voice_name} attempt={attempt+1} size={len(raw_audio)}")
                    return Response(
                        content=_build_wav(raw_audio),
                        media_type="audio/wav"
                    )
                else:
                    finish = getattr(candidate, "finish_reason", "unknown") if candidate else "no candidate"
                    last_error = f"Empty response (voice={voice_name}, finish={finish})"
                    print(f"[TTS] {last_error}")

            except Exception as e:
                last_error = str(e)
                print(f"[TTS] voice={voice_name} attempt={attempt+1} error: {e}")
                if attempt == 0:
                    # Small delay before retry
                    await asyncio.sleep(1.0)

    # All voices and retries exhausted
    print(f"[TTS] All voices failed. Last error: {last_error}")
    raise HTTPException(status_code=500, detail=f"TTS unavailable: {last_error}")