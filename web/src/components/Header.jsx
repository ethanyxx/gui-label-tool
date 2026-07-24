const PHASES = {
  idle: { dot: "bg-slate-500", text: "Idle", cls: "text-slate-400" },
  starting: {
    dot: "bg-amber-400 animate-pulse",
    text: "Starting…",
    cls: "text-amber-300",
  },
  running: {
    dot: "bg-emerald-400 animate-pulse",
    text: "Recording",
    cls: "text-emerald-300",
  },
  stopping: {
    dot: "bg-amber-400 animate-pulse",
    text: "Stopping…",
    cls: "text-amber-300",
  },
  paused: { dot: "bg-yellow-400", text: "Review", cls: "text-yellow-300" },
  finalizing: {
    dot: "bg-amber-400 animate-pulse",
    text: "Saving…",
    cls: "text-amber-300",
  },
  disconnected: {
    dot: "bg-red-500",
    text: "Services unreachable",
    cls: "text-red-400",
  },
};

export default function Header({ phase, eventCounter, session }) {
  const p = PHASES[phase] || PHASES.idle;
  return (
    <header className="flex items-center gap-4 border-b border-slate-800 bg-slate-900/80 px-5 py-3">
      <h1 className="text-lg font-semibold tracking-tight text-slate-100">
        GUI Label Tool
      </h1>
      <span className={`flex items-center gap-2 text-sm ${p.cls}`}>
        <span className={`h-2.5 w-2.5 rounded-full ${p.dot}`} />
        {p.text}
      </span>
      {session?.annotation_id && (
        <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-400">
          {session.annotation_id}
        </span>
      )}
      <div className="ml-auto text-sm text-slate-400">
        Events: <span className="font-mono text-slate-200">{eventCounter}</span>
      </div>
    </header>
  );
}
