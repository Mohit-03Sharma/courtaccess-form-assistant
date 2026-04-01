import type { Field } from "../api";

interface Props {
  fields: Field[];
  answers: Record<string, string | null>;
  currentField: string;
  onJumpToField: (fieldName: string) => void;
}

export default function FieldProgress({
  fields, answers, currentField, onJumpToField
}: Props) {
  const filled = Object.values(answers).filter(v => v != null && v !== "").length;
  const total = fields.length;
  const pct = total > 0 ? Math.round((filled / total) * 100) : 0;

  return (
    <div className="w-72 bg-white border-l border-gray-200 p-4 flex flex-col gap-3 overflow-y-auto">
      <div>
        <div className="flex justify-between text-sm text-gray-600 mb-1">
          <span className="font-medium">Progress</span>
          <span>{filled}/{total} fields</span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-2">
          <div
            className="bg-blue-500 h-2 rounded-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <p className="text-xs text-gray-400">
        Click any filled field to go back and change it.
      </p>

      <div className="flex flex-col gap-1">
        {fields.map((f) => {
          const answer = answers[f.name];
          const isDone = answer != null && answer !== "";
          const isSkipped = f.name in answers && (answer == null || answer === "");
          const isCurrent = f.name === currentField;

          return (
            <div
              key={f.name}
              onClick={() => isDone && onJumpToField(f.name)}
              className={`flex items-start gap-2 p-2 rounded-lg text-sm transition-colors
                ${isCurrent ? "bg-blue-50 border border-blue-200" : ""}
                ${isDone ? "opacity-70 cursor-pointer hover:bg-gray-50 hover:opacity-100" : ""}
                ${isSkipped ? "opacity-40" : ""}
              `}
              title={isDone ? "Click to edit this field" : undefined}
            >
              <span className="mt-0.5 text-base leading-none">
                {isDone ? "✅" : isSkipped ? "⏭️" : isCurrent ? "✏️" : "⬜"}
              </span>
              <div className="flex flex-col min-w-0">
                <span className={`font-medium truncate
                  ${isDone ? "line-through text-gray-400" : "text-gray-700"}
                `}>
                  {f.label}
                </span>
                {isDone && (
                  <span className="text-xs text-gray-400 truncate max-w-[180px]">
                    {answers[f.name]}
                  </span>
                )}
                {isSkipped && (
                  <span className="text-xs text-gray-400">skipped</span>
                )}
                {f.required && !isDone && !isSkipped && (
                  <span className="text-xs text-red-400">required</span>
                )}
              </div>
              {isDone && (
                <span className="ml-auto text-xs text-blue-400 shrink-0">edit</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}