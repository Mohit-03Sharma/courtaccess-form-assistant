import { useState, useRef, useEffect } from "react";
import { sendMessage } from "../api";
import VoiceButton from "./VoiceButton";

const BASE = "http://localhost:8000";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface Props {
  sessionId: string;
  currentField: string;
  language: string;
  onFieldUpdate: (updates: Record<string, string | null>) => void;
  onNextField: (field: string) => void;
  onVoiceFieldUpdate: (updates: Record<string, string | null>, nextField: string | null) => void;
  jumpToField: string | null;       // set by parent when user clicks a field in sidebar
  onJumpHandled: () => void;        // called after jump message is sent
}

export default function ChatInterface({
  sessionId, currentField, language,
  onFieldUpdate, onNextField, onVoiceFieldUpdate,
  jumpToField, onJumpHandled,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const greetingFetched = useRef(false);

  // ── Fetch greeting once on mount ─────────────────────────────────
  useEffect(() => {
    if (greetingFetched.current || !sessionId) return;
    greetingFetched.current = true;
    fetch(`${BASE}/api/session/${sessionId}/greeting`)
      .then(r => r.json())
      .then(data => {
        if (data.reply) {
          setMessages([{ role: "assistant", content: data.reply }]);
        }
      })
      .catch(() => {});
  }, [sessionId]);

  // ── Handle jump-to-field from sidebar ────────────────────────────
  useEffect(() => {
    if (!jumpToField) return;
    const msg = `I want to change my answer for: ${jumpToField}`;
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    setMessages(prev => [...prev, {
      role: "assistant",
      content: `Sure! Let's revisit **${jumpToField}**. What would you like to change it to?`
    }]);
    onJumpHandled();
  }, [jumpToField, onJumpHandled]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    if (!input.trim() || loading) return;
    const userMsg = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);
    try {
      const res = await sendMessage({
        session_id: sessionId,
        current_field: currentField,
        message: userMsg,
        language,
        history: messages.map(m => ({
          role: m.role === "assistant" ? "model" : "user",
          content: m.content
        })),
      });

      if (res.reply) {
        setMessages(prev => [...prev, { role: "assistant", content: res.reply }]);
      }
      if (res.field_updates) onFieldUpdate(res.field_updates);
      if (res.next_field && res.next_field !== currentField) onNextField(res.next_field);
    } catch {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: "Something went wrong. Please try again."
      }]);
    } finally {
      setLoading(false);
    }
  }

  function handleVoiceTranscript(role: "user" | "assistant", text: string) {
    setMessages(prev => [...prev, { role, content: text }]);
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-12 text-sm">
            Type or speak to start filling your form
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[78%] rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap
              ${m.role === "user"
                ? "bg-blue-500 text-white rounded-br-sm"
                : "bg-white border border-gray-200 text-gray-800 rounded-bl-sm"
              }`}>
              {m.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-2 text-sm text-gray-400">
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="p-3 border-t border-gray-200 bg-white">
        <div className="flex gap-2">
          <input
            className="flex-1 border border-gray-300 rounded-xl px-4 py-2 text-sm outline-none focus:border-blue-400"
            placeholder="Type your answer..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleSend()}
            disabled={loading}
          />
          <VoiceButton
            sessionId={sessionId}
            currentField={currentField}
            language={language}
            history={messages.map(m => ({
              role: m.role === "assistant" ? "model" : "user",
              content: m.content
            }))}
            onFieldUpdate={onVoiceFieldUpdate}
            onTranscript={handleVoiceTranscript}
            onError={(msg) => console.error("Voice error:", msg)}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="bg-blue-500 hover:bg-blue-600 disabled:opacity-40 text-white px-4 py-2 rounded-xl text-sm font-medium transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}