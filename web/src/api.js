// Thin wrappers around the frontend service's /api endpoints.

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${path} -> HTTP ${resp.status}`);
  return resp.json();
}

export const getConfig = () => request("/api/config");
export const getStatus = () => request("/api/status");
export const getEvents = () => request("/api/events");

export const startSession = (annotatorId, task) =>
  request("/api/session/start", {
    method: "POST",
    body: JSON.stringify({ annotator_id: annotatorId, task }),
  });

export const freezeSession = () =>
  request("/api/session/freeze", { method: "POST" });

export const finalizeSession = (excluded) =>
  request("/api/session/finalize", {
    method: "POST",
    body: JSON.stringify({ excluded }),
  });
