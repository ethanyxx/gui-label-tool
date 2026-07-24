// Per-event-type presentation: icon, accent color and a one-line description.

const META = {
  left_click: { icon: "🖱️", color: "#3b82f6", label: "Left click" },
  right_click: { icon: "🖱️", color: "#8b5cf6", label: "Right click" },
  double_click: { icon: "🖱️", color: "#06b6d4", label: "Double click" },
  middle_click: { icon: "🖱️", color: "#64748b", label: "Middle click" },
  text_input: { icon: "⌨️", color: "#10b981", label: "Text input" },
  key_press: { icon: "⌨️", color: "#6366f1", label: "Key press" },
  scroll: { icon: "🖲️", color: "#f59e0b", label: "Scroll" },
  hotkey: { icon: "⌨️", color: "#ef4444", label: "Hotkey" },
};

const CLICK_TYPES = new Set([
  "left_click",
  "right_click",
  "double_click",
  "middle_click",
]);

export function describeEvent(evt) {
  const type = evt.type || "unknown";
  const meta = META[type] || { icon: "📌", color: "#888888", label: type };
  const data = evt.event_data || {};
  let desc = type;

  if (CLICK_TYPES.has(type)) {
    const pos = evt.screen_position || data.position || {};
    desc = `(${pos.x ?? "?"}, ${pos.y ?? "?"})`;
  } else if (type === "text_input") {
    const text = data.text || "";
    desc = `"${text.length > 40 ? text.slice(0, 40) + "…" : text}"`;
  } else if (type === "key_press") {
    const count = data.count || 1;
    desc = `${data.key || "?"}${count > 1 ? ` ×${count}` : ""}`;
  } else if (type === "scroll") {
    desc = `${data.direction || "?"} ×${data.amount ?? "?"}`;
  } else if (type === "hotkey") {
    desc = data.combo || (data.keys || []).join(" + ") || "?";
  }

  return { ...meta, desc };
}
