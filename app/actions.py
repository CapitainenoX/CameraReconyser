"""Moteur d'actions : execute des chaines d'actions personnalisables."""
from __future__ import annotations

import datetime
import os
import subprocess
import threading
import time
import wave
import webbrowser
from pathlib import Path
from typing import Any, Callable

import numpy as np
import requests
import sounddevice as sd

from . import models
from .config import DATA_DIR, SOUNDS_DIR

NotifyCb = Callable[[str, dict[str, Any]], None]


class AlarmPlayer:
    """Joue alarm.wav en boucle jusqu'a stop()."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._active = False
        self._path = models.ensure_default_alarm(SOUNDS_DIR)

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            with wave.open(str(self._path), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            self._active = False
            return
        while self._active:
            sd.play(audio, rate)
            sd.wait()

    def stop(self) -> None:
        self._active = False
        try:
            sd.stop()
        except Exception:
            pass


class ActionEngine:
    def __init__(self, tts, notify: NotifyCb) -> None:
        self.tts = tts
        self.notify = notify
        self.alarm = AlarmPlayer()

    def run_actions(self, actions: list[dict[str, Any]], context: dict[str, Any] | None = None) -> None:
        threading.Thread(target=self._run, args=(actions, context or {}), daemon=True).start()

    def _run(self, actions: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        for action in actions:
            try:
                self._exec_one(action, ctx)
            except Exception as exc:
                self.notify("toast", {"level": "error", "message": f"Action echouee: {exc}"})

    def _fmt(self, text: str, ctx: dict[str, Any]) -> str:
        return text.replace("{name}", str(ctx.get("name", "")))

    def _exec_one(self, action: dict[str, Any], ctx: dict[str, Any]) -> None:
        kind = action.get("type", "")
        if kind == "say":
            self.tts.say(self._fmt(action.get("text", ""), ctx))
        elif kind == "open_url":
            url = action.get("url", "").strip()
            if url:
                webbrowser.open(url)
        elif kind == "launch_app":
            path = action.get("path", "").strip()
            if path:
                subprocess.Popen(path, shell=True)
        elif kind == "alarm":
            self.alarm.start()
            self.notify("alarm", {"active": True})
        elif kind == "stop_alarm":
            self.alarm.stop()
            self.notify("alarm", {"active": False})
        elif kind == "keys":
            combo = action.get("combo", "").strip()
            if combo:
                import pyautogui
                keys = [k.strip() for k in combo.replace("+", " ").split() if k.strip()]
                if keys:
                    pyautogui.hotkey(*keys)
        elif kind == "notification":
            self.notify("toast", {"level": "info", "message": self._fmt(action.get("text", ""), ctx)})
        elif kind == "shell":
            cmd = action.get("command", "").strip()
            if cmd:
                subprocess.Popen(cmd, shell=True)
        elif kind == "delay":
            time.sleep(max(0, int(action.get("ms", 0))) / 1000.0)
        elif kind == "type_text":
            text = self._fmt(action.get("text", ""), ctx)
            if text:
                import pyautogui
                pyautogui.typewrite(text, interval=0.01)
        elif kind == "open_folder":
            path = action.get("path", "").strip()
            if path and os.path.exists(path):
                os.startfile(path)  # type: ignore[attr-defined]
        elif kind == "screenshot":
            import pyautogui
            shots = DATA_DIR / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            name = datetime.datetime.now().strftime("shot_%Y%m%d_%H%M%S.png")
            pyautogui.screenshot(str(shots / name))
            self.notify("toast", {"level": "ok", "message": f"Capture : {name}"})
        elif kind == "http_request":
            url = action.get("url", "").strip()
            if url:
                method = (action.get("method", "GET") or "GET").upper()
                requests.request(method, url, timeout=10)
        elif kind == "media":
            key = {"play": "playpause", "pause": "playpause", "next": "nexttrack",
                   "prev": "prevtrack", "stop": "stop"}.get(action.get("key", "play"), "playpause")
            import pyautogui
            pyautogui.press(key)
        elif kind == "volume":
            import pyautogui
            cmd = action.get("dir", "up")
            if cmd == "mute":
                pyautogui.press("volumemute")
            else:
                key = "volumeup" if cmd == "up" else "volumedown"
                for _ in range(int(action.get("steps", 4))):
                    pyautogui.press(key)
        elif kind == "lock":
            import ctypes
            ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
