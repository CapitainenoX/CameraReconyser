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
        self.fire_rule: Callable[[str], bool] | None = None  # branché par le serveur

    def run_actions(self, actions: list[dict[str, Any]], context: dict[str, Any] | None = None) -> None:
        threading.Thread(target=self._run, args=(actions, context or {}), daemon=True).start()

    def _run(self, actions: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
        for action in actions:
            try:
                self._exec_one(action, ctx)
            except Exception as exc:
                self.notify("toast", {"level": "error", "message": f"Action echouee: {exc}"})

    def _fmt(self, text: str, ctx: dict[str, Any]) -> str:
        out = text.replace("{name}", str(ctx.get("name", "")))
        out = out.replace("{gesture}", str(ctx.get("gesture", "")))
        out = out.replace("{sequence}", str(ctx.get("sequence", "")))
        out = out.replace("{text}", str(ctx.get("text", "")))
        now = datetime.datetime.now()
        out = out.replace("{time}", now.strftime("%H:%M"))
        out = out.replace("{date}", now.strftime("%d/%m/%Y"))
        return out

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
        elif kind == "clipboard":
            text = self._fmt(action.get("text", ""), ctx)
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            subprocess.run("clip", input=text.encode("utf-16le"), creationflags=flags)
        elif kind == "paste":
            import pyautogui
            pyautogui.hotkey("ctrl", "v")
        elif kind == "mouse_click":
            import pyautogui
            btn = action.get("button", "left")
            clicks = 2 if btn == "double" else 1
            real = "left" if btn == "double" else btn
            x, y = action.get("x", ""), action.get("y", "")
            if str(x).strip() and str(y).strip():
                pyautogui.click(int(x), int(y), clicks=clicks, button=real)
            else:
                pyautogui.click(clicks=clicks, button=real)
        elif kind == "mouse_move":
            import pyautogui
            pyautogui.moveTo(int(action.get("x", 0)), int(action.get("y", 0)), duration=0.15)
        elif kind == "window":
            import pyautogui
            op = action.get("op", "minimize_all")
            if op == "minimize_all":
                pyautogui.hotkey("win", "d")
            elif op == "maximize":
                pyautogui.hotkey("win", "up")
            elif op == "close":
                pyautogui.hotkey("alt", "f4")
            elif op == "switch":
                pyautogui.hotkey("alt", "tab")
        elif kind == "play_sound":
            self._play_wav(action.get("path", "").strip())
        elif kind == "say_time":
            now = datetime.datetime.now()
            self.tts.say(f"Il est {now.strftime('%H heures %M')}")
        elif kind == "run_rule":
            rid = action.get("rule_id", "").strip()
            if rid and self.fire_rule is not None:
                self.fire_rule(rid)
        elif kind == "power":
            self._power(action.get("op", "sleep"))

    def _play_wav(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
            width = wf.getsampwidth()
        if width != 2:
            return
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(audio, rate)
        sd.wait()

    def _power(self, op: str) -> None:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        if op == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=flags)
        elif op == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=flags)
        elif op == "logoff":
            subprocess.Popen(["shutdown", "/l"], creationflags=flags)
        elif op == "hibernate":
            subprocess.Popen(["shutdown", "/h"], creationflags=flags)
        elif op == "sleep":
            subprocess.Popen(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                creationflags=flags,
            )
