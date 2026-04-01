/**
 * VoiceButton — REST-based voice input.
 *
 * Flow per turn:
 *   idle → [click] → recording  (MediaRecorder starts)
 *   recording → [click] → processing
 *     → POST /api/voice/transcribe  (get transcript)
 *     → POST /api/assistant/message (get reply + field updates)
 *     → POST /api/voice/speak       (get WAV audio, play it)
 *   processing → idle
 */

import { useState, useRef, useCallback } from "react";
import { sendMessage, transcribeAudio, speakText } from "../api";

type S = "idle" | "recording" | "processing";

interface Props {
  sessionId: string;
  currentField: string;
  language: string;
  history: { role: string; content: string }[];
  onTranscript: (role: "user" | "assistant", text: string) => void;
  onFieldUpdate: (updates: Record<string, string | null>, nextField: string | null) => void;
  onError: (msg: string) => void;
}

let _busy = false;

export default function VoiceButton({
  sessionId, currentField, language, history,
  onTranscript, onFieldUpdate, onError,
}: Props) {
  const [s, setS] = useState<S>("idle");
  const sRef      = useRef<S>("idle");
  const recRef    = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const setState = useCallback((v: S) => { sRef.current = v; setS(v); }, []);

  // ── Play WAV blob ─────────────────────────────────────────────────
  async function playBlob(blob: Blob) {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    await new Promise<void>(resolve => {
      audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
      audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
      audio.play().catch(() => resolve());
    });
  }

  // ── Start recording ───────────────────────────────────────────────
  const start = useCallback(async () => {
    if (_busy) return;
    _busy = true;
    chunksRef.current = [];

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
    } catch (e: any) {
      onError(e?.message ?? "Mic denied.");
      _busy = false;
      return;
    }

    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus" : "audio/webm";
    const rec = new MediaRecorder(stream, { mimeType: mime });
    recRef.current = rec;

    rec.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data); };
    rec.start(250);
    setState("recording");
  }, [onError, setState]);

  // ── Stop → transcribe → message → speak ──────────────────────────
  const stop = useCallback(async () => {
    const rec = recRef.current;
    if (!rec) return;
    setState("processing");

    // Stop recorder and mic
    await new Promise<void>(resolve => {
      rec.onstop = () => resolve();
      rec.stop();
    });
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    recRef.current = null;

    try {
      // 1. Transcribe
      const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" });
      let transcript: string;
      try {
        transcript = await transcribeAudio(audioBlob);
      } catch {
        onError("Transcription failed. Please try again.");
        setState("idle");
        _busy = false;
        return;
      }

      if (!transcript.trim()) {
        onError("No speech detected. Please try again.");
        setState("idle");
        _busy = false;
        return;
      }

      onTranscript("user", transcript);

      // 2. Send to assistant
      const res = await sendMessage({
        session_id:    sessionId,
        current_field: currentField,
        message:       transcript,
        language,
        history,
      });

      if (res.reply) onTranscript("assistant", res.reply);
      if (res.field_updates) onFieldUpdate(res.field_updates, res.next_field);

      // 3. Speak reply (best-effort — failure doesn't break the flow)
      if (res.reply) {
        try {
          const audioReply = await speakText(res.reply, language);
          await playBlob(audioReply);
        } catch {
          // TTS failed — reply text already shown in chat, no further action needed
        }
      }
    } catch (e: any) {
      onError(e?.message ?? "Something went wrong.");
    } finally {
      setState("idle");
      _busy = false;
    }
  }, [sessionId, currentField, language, history,
      onTranscript, onFieldUpdate, onError, setState]);

  // ── Click ─────────────────────────────────────────────────────────
  const click = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (sRef.current === "idle")      start();
    else if (sRef.current === "recording") stop();
  }, [start, stop]);

  // ── Render ────────────────────────────────────────────────────────
  const label = s === "idle"       ? "Give Input"
    : s === "recording"            ? "Listening… (tap to send)"
    :                                "Processing…";

  const cls = s === "idle"         ? "bg-indigo-500 hover:bg-indigo-600"
    : s === "recording"            ? "bg-red-500 hover:bg-red-600 animate-pulse"
    :                                "bg-gray-400 cursor-not-allowed";

  return (
    <button
      onClick={click}
      disabled={s === "processing"}
      className={`${cls} text-white px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2 transition-colors`}
    >
      <span>{s === "recording" ? "🔴" : "🎤"}</span>
      <span>{label}</span>
    </button>
  );
}