import { useState, useEffect } from "react";
import { listForms, createSession, exportForm, getPdfUrl } from "./api";
import type { FormInfo, SessionResponse } from "./api";
import ChatInterface from "./components/ChatInterface";
import PdfViewer from "./components/PdfViewer";
import FieldProgress from "./components/FieldProgress";

type Screen = "select" | "chat";

export default function App() {
  const [screen, setScreen] = useState<Screen>("select");
  const [forms, setForms] = useState<FormInfo[]>([]);
  const [selectedForm, setSelectedForm] = useState<FormInfo | null>(null);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [answers, setAnswers] = useState<Record<string, string | null>>({});
  const [currentField, setCurrentField] = useState("");
  const [language, setLanguage] = useState("en");
  const [loading, setLoading] = useState(false);
  const [exported, setExported] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [jumpToField, setJumpToField] = useState<string | null>(null);

  useEffect(() => {
    listForms().then(setForms);
  }, []);

  async function handleSelectForm(form: FormInfo) {
    setSelectedForm(form);
    setLoading(true);
    try {
      const s = await createSession(form.form_id);
      setSession(s);
      setCurrentField(s.fields[0]?.name ?? "");
      setAnswers({});
      setExported(false);
      setScreen("chat");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    if (!session || exporting) return;
    setExporting(true);
    try {
      const blob = await exportForm(session.session_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${selectedForm?.form_id}_filled.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      setExported(true);
    } catch (e) {
      console.error("Export error:", e);
    } finally {
      setExporting(false);
    }
  }

  // Required fields are only "filled" if their value is non-null and non-empty
  const allRequired = session?.fields.filter(f => f.required) ?? [];
  const allRequiredFilled = allRequired.every(
    f => answers[f.name] != null && answers[f.name] !== ""
  );

  const filledCount = Object.values(answers).filter(v => v != null && v !== "").length;

  if (screen === "select") {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-8 p-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-800">CourtAccess</h1>
          <p className="text-gray-500 mt-2">Select a form to fill with AI assistance</p>
        </div>

        <div className="flex flex-col gap-2 w-full max-w-sm">
          <label className="text-sm font-medium text-gray-600">Language</label>
          <select
            value={language}
            onChange={e => setLanguage(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm outline-none"
          >
            <option value="en">English</option>
            <option value="es">Español</option>
            <option value="pt">Português</option>
          </select>
        </div>

        <div className="grid gap-4 w-full max-w-2xl">
          {forms.map(form => (
            <button
              key={form.form_id}
              onClick={() => handleSelectForm(form)}
              disabled={loading}
              className="bg-white border border-gray-200 hover:border-blue-400 hover:shadow-md rounded-2xl p-6 text-left transition-all disabled:opacity-50"
            >
              <div className="font-semibold text-gray-800">{form.name}</div>
              <div className="text-sm text-gray-400 mt-1">Massachusetts Trial Court</div>
              <div className="mt-3 text-xs text-blue-500 font-medium">
                {loading ? "Loading..." : "Start filling →"}
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-gray-50 overflow-hidden">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between shrink-0 min-w-0 gap-2">
        <div className="flex items-center gap-4">
          <button
            onClick={() => setScreen("select")}
            className="text-gray-400 hover:text-gray-600 text-sm"
          >
            ← Back
          </button>
          <div>
            <h1 className="font-semibold text-gray-800 text-sm">{session?.form_name}</h1>
            <p className="text-xs text-gray-400">
              {filledCount}/{session?.fields.length ?? 0} fields filled
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <select
            value={language}
            onChange={e => setLanguage(e.target.value)}
            className="border border-gray-300 rounded-lg px-2 py-1 text-xs outline-none"
          >
            <option value="en">English</option>
            <option value="es">Español</option>
            <option value="pt">Português</option>
          </select>

          {/* Export — only shown when all required fields are filled */}
          {allRequiredFilled && (
            <button
              onClick={handleExport}
              disabled={exporting}
              className="bg-green-500 hover:bg-green-600 disabled:opacity-50 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            >
              {exported ? "Downloaded ✓" : "Download form"}
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-96 shrink-0 flex flex-col border-r border-gray-200 bg-white overflow-hidden">
          <ChatInterface
            sessionId={session?.session_id ?? ""}
            currentField={currentField}
            language={language}
            onFieldUpdate={updates => setAnswers(prev => ({ ...prev, ...updates }))}
            onNextField={setCurrentField}
            onVoiceFieldUpdate={(updates, nextField) => {
              setAnswers(prev => ({ ...prev, ...updates }));
              if (nextField) setCurrentField(nextField);
            }}
            jumpToField={jumpToField}
            onJumpHandled={() => setJumpToField(null)}
          />
        </div>

        <div className="flex-1 overflow-y-auto bg-gray-100 p-4">
          {selectedForm && session && (
            <div className="max-w-2xl mx-auto bg-white rounded-xl shadow-sm overflow-hidden">
              <PdfViewer
                pdfUrl={getPdfUrl(selectedForm.form_id)}
                fields={session.fields}
                currentField={currentField}
                answers={answers}
              />
            </div>
          )}
        </div>

        {session && (
          <FieldProgress
            fields={session.fields}
            answers={answers}
            currentField={currentField}
            onJumpToField={(fieldName) => {
              setCurrentField(fieldName);
              setJumpToField(fieldName);
            }}
          />
        )}
      </div>
    </div>
  );
}