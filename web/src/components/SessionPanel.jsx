function Field({ label, ...props }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <input
        className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 outline-none focus:border-sky-500 disabled:opacity-50"
        {...props}
      />
    </label>
  );
}

function Button({ children, variant = "primary", ...props }) {
  const styles = {
    primary:
      "bg-sky-600 hover:bg-sky-500 text-white disabled:bg-slate-700 disabled:text-slate-500",
    danger:
      "bg-rose-600 hover:bg-rose-500 text-white disabled:bg-slate-700 disabled:text-slate-500",
    success:
      "bg-emerald-600 hover:bg-emerald-500 text-white disabled:bg-slate-700 disabled:text-slate-500",
  };
  return (
    <button
      className={`w-full rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles[variant]}`}
      {...props}
    >
      {children}
    </button>
  );
}

export default function SessionPanel({
  phase,
  busy,
  annotatorId,
  task,
  onAnnotatorId,
  onTask,
  onStart,
  onFreeze,
  onFinalize,
}) {
  const hasSession = ["starting", "running", "stopping", "paused", "finalizing"].includes(
    phase,
  );

  return (
    <section className="space-y-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 className="text-sm font-semibold text-slate-300">Session</h2>
      <Field
        label="Annotator ID"
        placeholder="your-id"
        value={annotatorId}
        onChange={(e) => onAnnotatorId(e.target.value)}
        disabled={hasSession}
      />
      <Field
        label="Task"
        placeholder="what to record…"
        value={task}
        onChange={(e) => onTask(e.target.value)}
        disabled={hasSession}
      />
      <div className="space-y-2 pt-1">
        {(phase === "idle" || phase === "disconnected" || phase === "starting") && (
          <Button
            onClick={onStart}
            disabled={busy || phase === "disconnected" || !annotatorId.trim() || !task.trim()}
          >
            {phase === "starting" ? "Starting VM (~1 min)…" : "▶ Start recording"}
          </Button>
        )}
        {(phase === "running" || phase === "stopping") && (
          <Button variant="danger" onClick={onFreeze} disabled={busy}>
            {phase === "stopping" ? "Stopping…" : "⏸ Stop recording"}
          </Button>
        )}
        {(phase === "paused" || phase === "finalizing") && (
          <Button variant="success" onClick={onFinalize} disabled={busy}>
            {phase === "finalizing" ? "Saving…" : "✔ Confirm & export"}
          </Button>
        )}
      </div>
      {phase === "paused" && (
        <p className="text-xs leading-relaxed text-slate-500">
          Review the event stream on the right; exclude any accidental events,
          then confirm to export the trajectory.
        </p>
      )}
    </section>
  );
}
