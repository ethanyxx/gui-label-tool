import { useEffect, useRef } from "react";

export default function LogPanel({ lines }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <section className="flex min-h-0 flex-1 flex-col rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="mb-2 text-sm font-semibold text-slate-300">Activity log</h2>
      <div
        ref={ref}
        className="thin-scroll min-h-0 flex-1 overflow-y-auto rounded bg-slate-950/60 p-2 font-mono text-xs leading-relaxed text-slate-400"
      >
        {lines.length === 0 ? (
          <span className="text-slate-600">Ready.</span>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap">
              <span className="text-slate-600">[{l.time}]</span> {l.text}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
