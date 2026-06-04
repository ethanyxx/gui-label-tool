"""Turn the QEMU input-event trace into the semantic events we keep (pure).

This module owns the ``Event`` data model -- the thing qemu_log produces and
the annotation service consumes over HTTP -- plus the two passes that build it:

- :class:`QemuEventParser` decodes a single ``input_event`` trace line into a
  raw :class:`Event` (mouse position + modifier state);
- :class:`EventMerger` merges that raw stream into semantic actions: consecutive
  characters into text, repeated keys into a count, same-direction scrolls into
  one, two close left clicks into a double click.

The raw Event between the two is a private intermediate with no external
consumer, which is why both stages live here. Everything is pure: no IO, no
screenshots, no persistence, no threading -- both stages are unit-testable in
isolation. The runtime that tails the file and drives them on a single thread is
:class:`~gui_label_tool.qemu_log.pipeline.QemuLogPipeline`.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel

from gui_label_tool.qemu_log import keymap

# ============= The Event data model =============
#
# Conventions:
# - Enums subclass ``str`` and their values must match the historical strings
#   exactly (the persisted JSON and the frontend depend on them).
# - ``position`` holds raw QEMU coordinates (0..32767), independent of screen
#   resolution; the annotation stage scales them to pixels once it has a
#   screenshot (the only place the resolution is known).


class EventType(str, Enum):
    """Action type."""

    MOUSE_CLICK = "mouse_click"
    SCROLL = "scroll"
    TEXT_INPUT = "text_input"
    KEY_PRESS = "key_press"
    HOTKEY = "hotkey"


class ClickType(str, Enum):
    """Mouse click subtype."""

    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    DOUBLE = "double"


def now_iso() -> str:
    """Current time as an ISO-8601 string."""
    return datetime.now().isoformat()


class Event(BaseModel):
    """A single recorded action.

    All fields except ``type`` / ``timestamp`` are optional; each action type
    only populates the fields relevant to it.
    """

    type: str
    position: Optional[Dict[str, int]] = None
    text: Optional[str] = None
    length: Optional[int] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    button: Optional[str] = None
    click_type: Optional[str] = None  # left / right / double / middle

    # hotkey / key_press
    keys: Optional[List[str]] = None  # hotkey: ["Ctrl", "C"]
    combo: Optional[str] = None  # hotkey: "Ctrl+C"
    key: Optional[str] = None  # key_press: "enter" / "tab" / ...
    count: Optional[int] = None  # key_press merge count (None at parse time)

    timestamp: str

    # ---- Factory constructors ----

    @classmethod
    def mouse_click(
        cls,
        position: Dict[str, int],
        click_type: str,
        timestamp: Optional[str] = None,
    ) -> "Event":
        return cls(
            type=EventType.MOUSE_CLICK.value,
            button=click_type,
            click_type=click_type,
            position=position,
            timestamp=timestamp or now_iso(),
        )

    @classmethod
    def scroll(
        cls,
        direction: str,
        position: Dict[str, int],
        amount: int = 1,
        timestamp: Optional[str] = None,
    ) -> "Event":
        return cls(
            type=EventType.SCROLL.value,
            direction=direction,
            amount=amount,
            position=position,
            timestamp=timestamp or now_iso(),
        )

    @classmethod
    def text_input(
        cls,
        text: str,
        position: Dict[str, int],
        timestamp: Optional[str] = None,
    ) -> "Event":
        return cls(
            type=EventType.TEXT_INPUT.value,
            text=text,
            length=len(text),
            position=position,
            timestamp=timestamp or now_iso(),
        )

    @classmethod
    def key_press(
        cls,
        key: str,
        position: Dict[str, int],
        timestamp: Optional[str] = None,
    ) -> "Event":
        return cls(
            type=EventType.KEY_PRESS.value,
            key=key,
            position=position,
            timestamp=timestamp or now_iso(),
        )

    @classmethod
    def hotkey(
        cls,
        keys: List[str],
        combo: str,
        position: Dict[str, int],
        timestamp: Optional[str] = None,
    ) -> "Event":
        return cls(
            type=EventType.HOTKEY.value,
            keys=keys,
            combo=combo,
            position=position,
            timestamp=timestamp or now_iso(),
        )


# QEMU absolute axes range over 0..32767 (a normalized device coordinate that
# always spans the full axis, independent of screen resolution). The merger uses
# this below to express the double-click distance as a fraction of the axis; the
# annotation stage uses it to scale a position onto a screenshot.
QEMU_MAX_X = 32767
QEMU_MAX_Y = 32767


# ============= Decode: one trace line -> one raw Event =============

# Regexes for QEMU trace lines.
_PATTERNS = {
    "mouse_abs": re.compile(
        r"input_event_abs con \d+, axis ([xy]), value (0x[0-9a-f]+)"
    ),
    "mouse_btn": re.compile(
        r"input_event_btn con \d+, "
        r"button (left|right|middle|wheel-up|wheel-down), down ([01])"
    ),
    "key": re.compile(r"input_event_key_qcode con \d+, key qcode (\w+), down ([01])"),
}


class QemuEventParser:
    """QEMU event parser (stateful but side-effect free)."""

    def __init__(self) -> None:
        self.mouse_position = {"x": 0, "y": 0}
        self.shift_pressed = False
        self.ctrl_pressed = False
        self.alt_pressed = False
        self.meta_pressed = False  # Win / Super / Meta
        self.caps_lock_on = False

    def reset(self) -> None:
        """Reset mouse position and modifier state."""
        self.mouse_position = {"x": 0, "y": 0}
        self.shift_pressed = False
        self.ctrl_pressed = False
        self.alt_pressed = False
        self.meta_pressed = False
        self.caps_lock_on = False

    def parse_line(self, line: str) -> Optional[Event]:
        """Parse one trace line into an Event, or None."""
        line = line.strip()
        if not line:
            return None

        # --- Mouse absolute position ---
        match = _PATTERNS["mouse_abs"].search(line)
        if match:
            axis, value = match.groups()
            self.mouse_position[axis] = int(value, 16)
            return None

        # --- Mouse buttons ---
        match = _PATTERNS["mouse_btn"].search(line)
        if match:
            return self._parse_mouse_button(*match.groups())

        # --- Keyboard ---
        match = _PATTERNS["key"].search(line)
        if match:
            return self._parse_key(match.group(1).lower(), match.group(2) == "1")

        return None

    # ---- Internal ----

    def _parse_mouse_button(self, button: str, down: str) -> Optional[Event]:
        # Left/right/middle clicks fire on release (down=0).
        if button in ("left", "right", "middle") and down == "0":
            return Event.mouse_click(self.mouse_position.copy(), click_type=button)

        # The wheel fires on press (down=1).
        if button in ("wheel-up", "wheel-down") and down == "1":
            direction = "up" if button == "wheel-up" else "down"
            return Event.scroll(direction, self.mouse_position.copy(), amount=1)

        return None

    def _parse_key(self, qcode: str, is_down: bool) -> Optional[Event]:
        # Modifiers only update state; they produce no event.
        if qcode in keymap.SHIFT_QCODES:
            self.shift_pressed = is_down
            return None
        if qcode in keymap.CTRL_QCODES:
            self.ctrl_pressed = is_down
            return None
        if qcode in keymap.ALT_QCODES:
            self.alt_pressed = is_down
            return None
        if qcode in keymap.META_QCODES:
            self.meta_pressed = is_down
            return None
        if qcode == "caps_lock" and is_down:
            self.caps_lock_on = not self.caps_lock_on
            return None

        # Ignore key-up for non-modifiers.
        if not is_down:
            return None

        pos = self.mouse_position.copy()

        # Hotkey when ctrl/alt/meta is held.
        if self.ctrl_pressed or self.alt_pressed or self.meta_pressed:
            return self._build_hotkey(qcode, pos)

        # Function keys (arrows / F1-F12, etc.) -> key_press.
        if qcode in keymap.FUNCTION_KEY_QCODES:
            return Event.key_press(keymap.FUNCTION_KEY_QCODES[qcode], pos)

        # Enter / Tab -> key_press.
        if qcode in keymap.SPECIAL_PRESS_QCODES:
            return Event.key_press(keymap.SPECIAL_PRESS_QCODES[qcode], pos)

        # Backspace / Delete -> key_press("backspace").
        if qcode in keymap.BACKSPACE_QCODES:
            return Event.key_press("backspace", pos)

        # Space -> text_input(" ") (participates in text merging).
        if qcode in keymap.SPACE_QCODES:
            return Event.text_input(" ", pos)

        # Regular character.
        char = self._resolve_char(qcode)
        if char and char not in ("", "\n"):
            return Event.text_input(char, pos)

        return None

    def _build_hotkey(self, qcode: str, pos: dict) -> Event:
        key_name = keymap.hotkey_key_name(qcode)
        modifiers = []
        if self.ctrl_pressed:
            modifiers.append("Ctrl")
        if self.alt_pressed:
            modifiers.append("Alt")
        if self.meta_pressed:
            modifiers.append("Win")
        if self.shift_pressed:
            modifiers.append("Shift")
        keys = modifiers + [key_name]
        combo = "+".join(keys) if modifiers else key_name
        return Event.hotkey(keys=keys, combo=combo, position=pos)

    def _resolve_char(self, qcode: str) -> str:
        char = keymap.CHAR_MAP.get(qcode, "")
        # Letter case via Shift XOR CapsLock.
        if char and "a" <= char <= "z":
            if self.shift_pressed ^ self.caps_lock_on:
                char = char.upper()
        # Shifted symbol.
        elif self.shift_pressed:
            shifted = keymap.SHIFT_MAP.get(qcode)
            if shifted is not None:
                char = shifted
        return char


# ============= Merge: raw Event stream -> semantic actions =============


class EventMerger:
    """Merge a raw Event stream into semantic actions, emitting via a callback.

    ``double_click_distance_ratio`` is a fraction of the (normalized) QEMU axis
    range, so it is resolution-independent (e.g. ``0.004`` ~= 0.4% of the axis).

    Driven by a single thread (the
    :class:`~gui_label_tool.qemu_log.pipeline.QemuLogPipeline` loop), so it needs
    no locks. The loop calls :meth:`submit` for each incoming event and
    :meth:`flush_due` regularly to emit pending merges whose window has elapsed;
    :meth:`flush_all` is called once on shutdown.
    """

    def __init__(
        self,
        emit: Callable[[Event], None],
        *,
        text_debounce: float,
        scroll_debounce: float,
        key_debounce: float,
        double_click_threshold: float,
        double_click_distance_ratio: float,
    ):
        self.emit = emit
        self.text_debounce = text_debounce
        self.scroll_debounce = scroll_debounce
        self.key_debounce = key_debounce
        self.double_click_threshold = double_click_threshold
        self.double_click_distance_ratio = double_click_distance_ratio

        # Each pending bucket holds (Event, last_activity_time). The Event keeps
        # the timestamp of its *first* sub-event (used later to find the
        # pre-action screenshot); last_activity_time governs the merge window.
        self._text: Optional[Event] = None
        self._text_ts = 0.0
        self._scroll: Optional[Event] = None
        self._scroll_ts = 0.0
        self._key: Optional[Event] = None
        self._key_ts = 0.0
        self._click: Optional[Event] = None
        self._click_ts = 0.0

    # ---- Intake ----

    def submit(self, event: Event, now: float) -> None:
        """Feed one raw event into the merge pipeline."""
        if event.type == EventType.TEXT_INPUT.value:
            self._on_text(event, now)
        elif event.type == EventType.SCROLL.value:
            self._on_scroll(event, now)
        elif event.type == EventType.MOUSE_CLICK.value:
            self._on_click(event, now)
        elif event.type == EventType.KEY_PRESS.value:
            self._on_key(event, now)
        else:
            # hotkey / unknown: no merge -- flush pending, then emit directly.
            self.flush_all()
            self.emit(event)

    def flush_due(self, now: float) -> None:
        """Emit any pending merge whose window has elapsed (call regularly)."""
        if self._text and now - self._text_ts >= self.text_debounce:
            self.emit(self._text)
            self._text = None
        if self._scroll and now - self._scroll_ts >= self.scroll_debounce:
            self.emit(self._scroll)
            self._scroll = None
        if self._key and now - self._key_ts >= self.key_debounce:
            self.emit(self._key)
            self._key = None
        if self._click and now - self._click_ts >= self.double_click_threshold:
            self._emit_click(self._click)
            self._click = None

    def flush_all(self) -> None:
        """Emit every pending merge (called on shutdown)."""
        if self._text:
            self.emit(self._text)
            self._text = None
        if self._scroll:
            self.emit(self._scroll)
            self._scroll = None
        if self._key:
            self.emit(self._key)
            self._key = None
        if self._click:
            self._emit_click(self._click)
            self._click = None

    # ---- Per-type handlers ----

    def _on_text(self, event: Event, now: float) -> None:
        if self._text and now - self._text_ts < self.text_debounce:
            merged = (self._text.text or "") + (event.text or "")
            self._text.text = merged
            self._text.length = len(merged)
        else:
            if self._text:
                self.emit(self._text)
            self._text = event
        self._text_ts = now

    def _on_scroll(self, event: Event, now: float) -> None:
        if event.amount is None:
            event.amount = 1
        if (
            self._scroll
            and now - self._scroll_ts < self.scroll_debounce
            and self._scroll.direction == event.direction
        ):
            self._scroll.amount = (self._scroll.amount or 1) + (event.amount or 1)
        else:
            if self._scroll:
                self.emit(self._scroll)
            self._scroll = event
        self._scroll_ts = now

    def _on_key(self, event: Event, now: float) -> None:
        key_name = (event.key or "").lower()
        if not key_name:
            self.flush_all()
            self.emit(event)
            return
        event.count = event.count or 1
        if (
            self._key
            and now - self._key_ts < self.key_debounce
            and (self._key.key or "").lower() == key_name
        ):
            self._key.count = (self._key.count or 1) + (event.count or 1)
        else:
            if self._key:
                self.emit(self._key)
            self._key = event
        self._key_ts = now

    def _on_click(self, event: Event, now: float) -> None:
        click_type = event.click_type or ClickType.LEFT.value
        # Right / middle clicks never merge: flush a pending left, then emit.
        if click_type in (ClickType.RIGHT.value, ClickType.MIDDLE.value):
            if self._click:
                self._emit_click(self._click)
                self._click = None
            self.emit(event)
            return

        if (
            self._click
            and now - self._click_ts < self.double_click_threshold
            and self._is_near(self._click, event)
        ):
            # Two close left clicks -> double click, keeping the first's frame.
            self._click.click_type = ClickType.DOUBLE.value
            self._emit_click(self._click)
            self._click = None
            return

        if self._click:
            self._emit_click(self._click)
        self._click = event
        self._click_ts = now

    # ---- Helpers ----

    def _emit_click(self, event: Event) -> None:
        if not event.click_type:
            event.click_type = ClickType.LEFT.value
        self.emit(event)

    def _is_near(self, a: Event, b: Event) -> bool:
        pa = a.position or {}
        pb = b.position or {}
        dx = pb.get("x", 0) - pa.get("x", 0)
        dy = pb.get("y", 0) - pa.get("y", 0)
        dist_ratio = (dx * dx + dy * dy) ** 0.5 / QEMU_MAX_X
        return dist_ratio < self.double_click_distance_ratio


__all__ = [
    "Event",
    "EventType",
    "ClickType",
    "now_iso",
    "QEMU_MAX_X",
    "QEMU_MAX_Y",
    "QemuEventParser",
    "EventMerger",
]
