import { useState } from "react";

export default function VncPanel({ vncUrl, phase }) {
  const [nonce, setNonce] = useState(() => Date.now());

  const showDesktop =
    vncUrl && ["starting", "running", "stopping", "paused", "finalizing"].includes(phase);
  const src = vncUrl ? `${vncUrl}&_t=${nonce}` : null;

  return (
    <section className="flex flex-col rounded-lg border border-slate-800 bg-slate-900">
      <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2">
        <h2 className="text-sm font-semibold text-slate-300">
          Remote desktop <span className="font-normal text-slate-500">1920×1080</span>
        </h2>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setNonce(Date.now())}
            disabled={!showDesktop}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            ⟳ Reconnect
          </button>
          <a
            href={src ?? "#"}
            target="_blank"
            rel="noreferrer"
            className={`rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 ${
              showDesktop ? "" : "pointer-events-none opacity-40"
            }`}
          >
            ⧉ Open in tab
          </a>
        </div>
      </div>
      <div className="relative aspect-video w-full overflow-hidden rounded-b-lg bg-black">
        {showDesktop ? (
          <iframe
            key={nonce}
            src={src}
            title="Remote desktop"
            allow="fullscreen"
            className="h-full w-full border-0"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-600">
            Start a session to boot the virtual desktop
          </div>
        )}
        {phase === "starting" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
            <span className="text-sm text-slate-300">
              Booting the VM container… this takes about a minute
            </span>
          </div>
        )}
      </div>
    </section>
  );
}
