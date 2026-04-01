import { useEffect, useRef, useState, useCallback } from "react";
import type { Field } from "../api";
import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

interface Props {
  pdfUrl: string;
  fields: Field[];
  currentField: string;
  answers: Record<string, string | null>;
}

export default function PdfViewer({ pdfUrl, fields, currentField, answers }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // One canvas pair per page: [pdfCanvas, overlayCanvas]
  const [pages, setPages] = useState<any[]>([]);         // pdfjs page objects
  const [scale, setScale] = useState(1);
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  const overlayRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  const renderTasksRef = useRef<any[]>([]);

  // ── Load all pages ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setPages([]);
    pdfjsLib.getDocument(pdfUrl).promise.then(async pdf => {
      if (cancelled) return;
      const loaded: any[] = [];
      for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        if (cancelled) return;
        loaded.push(page);
      }
      setPages(loaded);
    });
    return () => { cancelled = true; };
  }, [pdfUrl]);

  // ── Compute scale once first page is loaded ───────────────────────
  useEffect(() => {
    if (!pages.length || !containerRef.current) return;
    const containerWidth = containerRef.current.clientWidth;
    const viewport = pages[0].getViewport({ scale: 1 });
    setScale(containerWidth / viewport.width);
  }, [pages]);

  // ── Render PDF pages onto canvases ────────────────────────────────
  useEffect(() => {
    if (!pages.length || scale === 1) return;

    // Cancel any in-flight renders
    renderTasksRef.current.forEach(t => t?.cancel());
    renderTasksRef.current = [];

    pages.forEach((page, idx) => {
      const canvas = canvasRefs.current[idx];
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const viewport = page.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const task = page.render({ canvasContext: ctx, viewport });
      renderTasksRef.current[idx] = task;
      task.promise.catch((err: any) => {
        if (err?.name !== "RenderingCancelledException") {
          console.error(`PDF render error page ${idx + 1}:`, err);
        }
      });
    });
  }, [pages, scale]);

  // ── Draw overlays ─────────────────────────────────────────────────
  const drawOverlays = useCallback(() => {
    if (!pages.length || scale === 1) return;

    pages.forEach((page, pageIdx) => {
      const canvas = overlayRefs.current[pageIdx];
      if (!canvas) return;

      const viewport = page.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Only draw fields that belong to this page (0-indexed page number)
      const pageFields = fields.filter(f => (f.page ?? 0) === pageIdx);

      pageFields.forEach(field => {
        if (!field.rect) return;
        const [x0, y0, x1, y1] = field.rect;
        const sx = x0 * scale;
        const sy = y0 * scale;
        const sw = (x1 - x0) * scale;
        const sh = (y1 - y0) * scale;

        const isCurrent = field.name === currentField;
        const answer = answers[field.name];
        const isFilled = answer != null && answer !== "" && answer !== "null";

        if (isCurrent) {
          ctx.strokeStyle = "#3b82f6";
          ctx.lineWidth = 2;
          ctx.strokeRect(sx, sy, sw, sh);
          ctx.fillStyle = "rgba(59, 130, 246, 0.10)";
          ctx.fillRect(sx, sy, sw, sh);
        }

        if (isFilled) {
          ctx.fillStyle = "rgba(240, 253, 244, 0.90)";
          ctx.fillRect(sx, sy, sw, sh);
          ctx.fillStyle = "#166534";
          ctx.font = `${Math.max(8, sh * 0.55)}px sans-serif`;
          ctx.textBaseline = "middle";
          const maxWidth = sw - 8;
          ctx.fillText(answer, sx + 4, sy + sh / 2, maxWidth);
        }
      });
    });
  }, [fields, currentField, answers, pages, scale]);

  useEffect(() => {
    drawOverlays();
  }, [drawOverlays]);

  if (!pages.length) {
    return (
      <div className="flex items-center justify-center h-48 text-sm text-gray-400">
        Loading PDF…
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex flex-col gap-2 w-full">
      {pages.map((_, idx) => (
        <div key={idx} className="relative w-full">
          <canvas
            ref={el => { canvasRefs.current[idx] = el; }}
            className="w-full block"
          />
          <canvas
            ref={el => { overlayRefs.current[idx] = el; }}
            className="absolute top-0 left-0 w-full h-full pointer-events-none"
          />
        </div>
      ))}
    </div>
  );
}