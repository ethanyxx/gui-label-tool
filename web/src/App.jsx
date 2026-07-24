import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api.js";
import Header from "./components/Header.jsx";
import SessionPanel from "./components/SessionPanel.jsx";
import LogPanel from "./components/LogPanel.jsx";
import VncPanel from "./components/VncPanel.jsx";
import EventStream from "./components/EventStream.jsx";

const now = () => new Date().toTimeString().slice(0, 8);
const excludedKey = (id) => `gui-label-tool:excluded:${id}`;

export default function App() {
  // Inputs.
  const [annotatorId, setAnnotatorId] = useState("");
  const [task, setTask] = useState("");

  // Server-derived state.
  const [serverPhase, setServerPhase] = useState("idle");
  const [session, setSession] = useState(null);
  const [eventCounter, setEventCounter] = useState(0);
  const [events, setEvents] = useState([]);
  const [deletedIds, setDeletedIds] = useState(new Set());
  const [vncUrl, setVncUrl] = useState(null);

  // Client-only state.
  const [action, setAction] = useState(null); // 'start' | 'freeze' | 'finalize'
  const [excludedIds, setExcludedIds] = useState(new Set());
  const [log, setLog] = useState([]);

  const restoredRef = useRef(false);

  const appendLog = useCallback((text) => {
    setLog((prev) => [
      ...prev.slice(-200),
      ...String(text)
        .split("\n")
        .filter((l) => l.trim() !== "")
        .map((l) => ({ time: now(), text: l })),
    ]);
  }, []);

  // Effective phase: in-flight actions override what the server reports.
  const phase =
    action === "start"
      ? "starting"
      : action === "freeze"
        ? "stopping"
        : action === "finalize"
          ? "finalizing"
          : serverPhase;

  // ---- Polling: status every second, events whenever a session exists ----
  useEffect(() => {
    let stop = false;

    const tick = async () => {
      try {
        const st = await api.getStatus();
        if (stop) return;
        setServerPhase(st.phase);
        setEventCounter(st.event_counter ?? 0);
        setSession(st.session?.annotation_id ? st.session : null);
        if (st.session?.vnc_url) setVncUrl(st.session.vnc_url);

        // One-time restore of an in-progress session after a page load.
        if (st.session?.annotation_id && !restoredRef.current) {
          restoredRef.current = true;
          setAnnotatorId((v) => v || st.session.annotator_id || "");
          setTask((v) => v || st.session.task || "");
          try {
            const saved = JSON.parse(
              localStorage.getItem(excludedKey(st.session.annotation_id)) || "[]",
            );
            setExcludedIds(new Set(saved));
          } catch {
            /* ignore corrupted storage */
          }
          appendLog("Restored an in-progress session");
        }

        if (st.session?.annotation_id) {
          const ev = await api.getEvents();
          if (stop) return;
          setEvents(ev.events || []);
          setDeletedIds(new Set(ev.deleted || []));
        } else {
          setEvents([]);
          setDeletedIds(new Set());
        }
      } catch {
        if (!stop) setServerPhase("disconnected");
      }
    };

    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [appendLog]);

  // Persist local exclusions so a refresh doesn't lose review work.
  useEffect(() => {
    if (session?.annotation_id) {
      localStorage.setItem(
        excludedKey(session.annotation_id),
        JSON.stringify([...excludedIds]),
      );
    }
  }, [excludedIds, session]);

  // ---- Actions ----
  const handleStart = async () => {
    setAction("start");
    appendLog("Starting session…");
    try {
      const res = await api.startSession(annotatorId, task);
      appendLog(res.message);
      if (res.success) {
        restoredRef.current = true;
        setVncUrl(res.vnc_url);
        setExcludedIds(new Set());
      }
    } catch (e) {
      appendLog(`✗ ${e.message}`);
    } finally {
      setAction(null);
    }
  };

  const handleFreeze = async () => {
    setAction("freeze");
    try {
      const res = await api.freezeSession();
      appendLog(res.message);
    } catch (e) {
      appendLog(`✗ ${e.message}`);
    } finally {
      setAction(null);
    }
  };

  const handleFinalize = async () => {
    setAction("finalize");
    try {
      const res = await api.finalizeSession([...excludedIds]);
      appendLog(res.message);
      if (res.success) {
        if (res.full_task_file) appendLog(`Saved to: ${res.full_task_file}`);
        if (session?.annotation_id)
          localStorage.removeItem(excludedKey(session.annotation_id));
        setExcludedIds(new Set());
        setAnnotatorId("");
        setTask("");
        setVncUrl(null);
        restoredRef.current = false;
      }
    } catch (e) {
      appendLog(`✗ ${e.message}`);
    } finally {
      setAction(null);
    }
  };

  const toggleExclude = (eventId) => {
    setExcludedIds((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  };

  return (
    <div className="flex h-full flex-col">
      <Header phase={phase} eventCounter={eventCounter} session={session} />

      <main className="grid min-h-0 flex-1 grid-cols-[280px_1fr_400px] gap-4 p-4">
        {/* Left: controls + log */}
        <div className="flex min-h-0 flex-col gap-4">
          <SessionPanel
            phase={phase}
            busy={action !== null}
            annotatorId={annotatorId}
            task={task}
            onAnnotatorId={setAnnotatorId}
            onTask={setTask}
            onStart={handleStart}
            onFreeze={handleFreeze}
            onFinalize={handleFinalize}
          />
          <LogPanel lines={log} />
        </div>

        {/* Center: remote desktop */}
        <div className="min-h-0 min-w-0">
          <VncPanel vncUrl={vncUrl} phase={phase} />
        </div>

        {/* Right: live event stream */}
        <EventStream
          events={events}
          deletedIds={deletedIds}
          excludedIds={excludedIds}
          canToggle={phase === "running" || phase === "paused"}
          onToggle={toggleExclude}
        />
      </main>
    </div>
  );
}
