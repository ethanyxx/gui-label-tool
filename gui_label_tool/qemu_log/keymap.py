"""QEMU qcode decode tables.

The qcode-to-character/key-name tables used by :mod:`parser` to translate raw
QEMU input into actions. These are QEMU-specific decoding details: swap the
virtualization backend and this table changes alongside the parser, while the
``Event`` action vocabulary it builds stays put.
"""

from __future__ import annotations

# qcode -> base character (Tab excluded; Tab is a key_press).
CHAR_MAP = {
    "a": "a",
    "b": "b",
    "c": "c",
    "d": "d",
    "e": "e",
    "f": "f",
    "g": "g",
    "h": "h",
    "i": "i",
    "j": "j",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "o",
    "p": "p",
    "q": "q",
    "r": "r",
    "s": "s",
    "t": "t",
    "u": "u",
    "v": "v",
    "w": "w",
    "x": "x",
    "y": "y",
    "z": "z",
    "0": "0",
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "dot": ".",
    "comma": ",",
    "minus": "-",
    "equal": "=",
    "semicolon": ";",
    "apostrophe": "'",
    "slash": "/",
    "backslash": "\\",
    "grave_accent": "",
    "left_bracket": "[",
    "right_bracket": "]",
    "ret": "\n",
    # Modifiers produce no character.
    "shift": "",
    "ctrl": "",
    "alt": "",
    "caps_lock": "",
    "meta": "",
    "meta_l": "",
    "meta_r": "",
    "super": "",
    "super_l": "",
    "super_r": "",
    # The following are not part of text input.
    "space": "",
    "backspace": "",
    "delete": "",
    "tab": "",
}

# Shifted symbol map.
SHIFT_MAP = {
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    "minus": "_",
    "equal": "+",
    "left_bracket": "{",
    "right_bracket": "}",
    "backslash": "|",
    "semicolon": ":",
    "apostrophe": '"',
    "comma": "<",
    "dot": ">",
    "slash": "?",
    "grave_accent": "~",
}

# Standalone key_press qcodes (not part of text merging).
SPECIAL_PRESS_QCODES = {
    "ret": "enter",
    "enter": "enter",
    "tab": "tab",
}

# Arrow keys / F1-F12 / other function keys -> key_press.
FUNCTION_KEY_QCODES = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "kp_up": "up",
    "kp_down": "down",
    "kp_left": "left",
    "kp_right": "right",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "esc": "escape",
    "escape": "escape",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "page_up": "page_up",
    "page_down": "page_down",
    "print": "print_screen",
    "scroll_lock": "scroll_lock",
    "pause": "pause",
    "num_lock": "num_lock",
    "menu": "menu",
}

# Backspace / space qcodes.
BACKSPACE_QCODES = {"backspace", "delete", "kp_delete"}
SPACE_QCODES = {"spc", "space", "kp_space"}

# qcode sets per modifier.
SHIFT_QCODES = {"shift", "shift_l", "shift_r"}
CTRL_QCODES = {"ctrl", "ctrl_l", "ctrl_r"}
ALT_QCODES = {"alt", "alt_l", "alt_r"}
META_QCODES = {"meta", "meta_l", "meta_r", "super", "super_l", "super_r"}

# Special display names for hotkeys.
_HOTKEY_NAME_MAP = {
    "ret": "ENTER",
    "enter": "ENTER",
    "space": "SPACE",
    "backspace": "BACKSPACE",
    "delete": "DELETE",
    "tab": "TAB",
    "esc": "ESC",
    "escape": "ESC",
    "up": "UP",
    "down": "DOWN",
    "left": "LEFT",
    "right": "RIGHT",
    "home": "HOME",
    "end": "END",
    "page_up": "PAGE_UP",
    "page_down": "PAGE_DOWN",
    "insert": "INSERT",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}


def hotkey_key_name(qcode: str) -> str:
    """Display name of a key inside a hotkey combo."""
    if len(qcode) == 1 and "a" <= qcode <= "z":
        return qcode.upper()
    if qcode.isdigit():
        return qcode
    return _HOTKEY_NAME_MAP.get(qcode, qcode.upper())
