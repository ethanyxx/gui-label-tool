import { describeEvent } from "../eventMeta.js";

function EventCard({ evt, excluded, canToggle, onToggle }) {
  const meta = describeEvent(evt);
  const time = (evt.timestamp || "").slice(11, 19);

  return (
    <div
      id={`event-${evt.event_id}`}
      className={`card-in flex gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3 transition-opacity ${
        excluded ? "opacity-40 grayscale" : ""
      }`}
      style={{ borderLeft: `4px solid ${meta.color}` }}
    >
      {evt.screenshot_url ? (
        <a href={evt.screenshot_url} target="_blank" rel="noreferrer" className="shrink-0">
          <img
            src={evt.screenshot_url}
            alt={`screenshot ${evt.event_id}`}
            loading="lazy"
            className="h-24 w-40 rounded border border-slate-800 object-cover"
          />
        </a>
      ) : (
        <div className="flex h-24 w-40 shrink-0 items-center justify-center rounded border border-slate-800 bg-slate-950 text-xs text-slate-600">
          no screenshot
        </div>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span>{meta.icon}</span>
          <span className="text-sm font-medium" style={{ color: meta.color }}>
            {meta.label}
          </span>
          <span className="font-mono text-xs text-slate-500">#{evt.event_id}</span>
          {excluded && (
            <span className="rounded bg-rose-900/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-300">
              excluded
            </span>
          )}
        </div>
        <div className="mt-1 truncate font-mono text-sm text-slate-300">{meta.desc}</div>
        <div className="mt-1 text-xs text-slate-500">{time}</div>
      </div>

      {canToggle && (
        <button
          onClick={() => onToggle(evt.event_id)}
          className={`self-center rounded border px-2 py-1 text-xs transition-colors ${
            excluded
              ? "border-emerald-700 text-emerald-400 hover:bg-emerald-900/40"
              : "border-rose-800 text-rose-400 hover:bg-rose-900/40"
          }`}
        >
          {excluded ? "Restore" : "Exclude"}
        </button>
      )}
    </div>
  );
}

export default function EventStream({ events, deletedIds, excludedIds, canToggle, onToggle }) {
  const allExcluded = new Set([...deletedIds, ...excludedIds]);
  const total = events.length;
  const excludedCount = [...allExcluded].filter((id) =>
    events.some((e) => e.event_id === id),
  ).length;

  return (
    <section className="flex min-h-0 flex-1 flex-col rounded-lg border border-slate-800 bg-slate-900">
      <div className="flex items-center gap-4 border-b border-slate-800 px-4 py-2 text-xs text-slate-400">
        <h2 className="text-sm font-semibold text-slate-300">Event stream</h2>
        <span className="ml-auto">
          Total <b className="font-mono text-slate-200">{total}</b>
        </span>
        <span>
          Kept <b className="font-mono text-emerald-300">{total - excludedCount}</b>
        </span>
        <span>
          Excluded <b className="font-mono text-rose-300">{excludedCount}</b>
        </span>
      </div>
      <div className="thin-scroll min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {total === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-600">
            No events yet — actions in the desktop will appear here
          </div>
        ) : (
          [...events]
            .reverse()
            .map((evt) => (
              <EventCard
                key={evt.event_id}
                evt={evt}
                excluded={allExcluded.has(evt.event_id)}
                canToggle={canToggle && !deletedIds.has(evt.event_id)}
                onToggle={onToggle}
              />
            ))
        )}
      </div>
    </section>
  );
}
