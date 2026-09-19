"""被控端键鼠注入与剪贴板(Windows 优先,非 Windows 自动降级)。"""

try:
    from pynput.keyboard import Controller as _KbController, Key, KeyCode
    from pynput.mouse import Button as _Button, Controller as _MouseController
    _PYNPUT_OK = True
except Exception:
    _PYNPUT_OK = False

try:
    import pyperclip
    _CLIP_OK = True
except Exception:
    _CLIP_OK = False


def _build_special():
    if not _PYNPUT_OK:
        return {}
    S = Key
    g = lambda name: getattr(S, name, None)  # noqa: E731
    m = {
        "Enter": S.enter, "NumpadEnter": S.enter, "Backspace": S.backspace,
        "Tab": S.tab, "Escape": S.esc, "Space": S.space,
        "ArrowUp": S.up, "ArrowDown": S.down, "ArrowLeft": S.left, "ArrowRight": S.right,
        "Home": S.home, "End": S.end, "PageUp": S.page_up, "PageDown": S.page_down,
        "Delete": S.delete, "Insert": S.insert,
        "ShiftLeft": S.shift, "ShiftRight": g("shift_r") or S.shift,
        "ControlLeft": S.ctrl, "ControlRight": g("ctrl_r") or S.ctrl,
        "AltLeft": S.alt, "AltRight": g("alt_gr") or S.alt,
        "MetaLeft": g("cmd") or S.alt, "MetaRight": g("cmd_r") or g("cmd") or S.alt,
        "CapsLock": g("caps_lock"), "NumLock": g("num_lock"),
        "ScrollLock": g("scroll_lock"), "Pause": g("pause"),
        "PrintScreen": g("print_screen"), "ContextMenu": g("menu"),
    }
    for i in range(1, 25):
        k = g(f"F{i}")
        if k is not None:
            m[f"F{i}"] = k
    for name, vk in {
        "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D, "NumpadMultiply": 0x6A,
        "NumpadDivide": 0x6F, "NumpadDecimal": 0x6E,
        "VolumeUp": 0xAF, "VolumeDown": 0xAE, "VolumeMute": 0xAD,
        "MediaTrackNext": 0xB0, "MediaTrackPrevious": 0xB1,
        "MediaStop": 0xB2, "MediaPlayPause": 0xB3,
    }.items():
        m[name] = KeyCode.from_vk(vk)
    return {k: v for k, v in m.items() if v is not None}


_MOUSE_BUTTONS = {}


class RemoteInput:
    def __init__(self):
        self.ok = _PYNPUT_OK
        if _PYNPUT_OK:
            self.mouse = _MouseController()
            self.kb = _KbController()
            self._special = _build_special()
            self._buttons = {
                "left": _Button.left, "right": _Button.right, "middle": _Button.middle,
            }

    # ---- 键盘 ----
    def key_event(self, code, key, kc, down):
        if not self.ok:
            return False
        k = self._resolve(code, key, kc)
        if k is None:
            return False
        try:
            (self.kb.press if down else self.kb.release)(k)
            return True
        except Exception:
            return False

    def _resolve(self, code, key, kc):
        if code and code in self._special:
            return self._special[code]
        if key and len(key) == 1 and key >= " ":
            return KeyCode.from_char(key)
        if kc and 0 < kc < 256:
            try:
                return KeyCode.from_vk(kc)
            except Exception:
                pass
        if code:
            if code.startswith("Key") and len(code) == 4 and code[3].isalpha():
                return KeyCode.from_char(code[3].lower())
            if code.startswith("Digit") and code[-1].isdigit():
                return KeyCode.from_char(code[-1])
            if code.startswith("Numpad") and code[-1].isdigit():
                return KeyCode.from_vk(0x60 + int(code[-1]))
        return None

    def type_text(self, text, backspace=0):
        """回删 backspace 个字符后输入文本(支持中文等 Unicode)。"""
        if not self.ok:
            return False
        try:
            for _ in range(max(0, int(backspace))):
                self.kb.tap(Key.backspace)
            if text:
                self.kb.type(text)
            return True
        except Exception:
            return False

    # ---- 鼠标 ----
    def mouse_move(self, x, y):
        if not self.ok:
            return
        self.mouse.position = (int(x), int(y))

    def mouse_button(self, name, down):
        if not self.ok:
            return
        b = self._buttons.get(name)
        if b is None:
            return
        (self.mouse.press if down else self.mouse.release)(b)

    def mouse_scroll(self, dx, dy):
        if not self.ok:
            return
        self.mouse.scroll(int(dx), int(dy))

    # ---- 剪贴板 ----
    def clipboard_get(self):
        if not _CLIP_OK:
            return ""
        try:
            return pyperclip.paste() or ""
        except Exception:
            return ""

    def clipboard_set(self, text):
        if not _CLIP_OK:
            return False
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            return False
