"""Synthese vocale via le binaire Piper (offline) avec file d'attente."""
from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import models
from .config import MODELS_DIR


def list_voices() -> list[str]:
    """Voix .onnx presentes dans data/models/tts/."""
    tts_dir = MODELS_DIR / "tts"
    if not tts_dir.exists():
        return []
    return sorted(p.name for p in tts_dir.glob("*.onnx"))


class TTSManager:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._voice_path: Path | None = None
        self._config_path: Path | None = None
        self._voice_name = ""
        self._volume = 1.0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self.load_voice("")

    @property
    def ready(self) -> bool:
        return (
            self._voice_path is not None
            and self._voice_path.exists()
            and models.PIPER_EXE.exists()
        )

    def set_volume(self, vol: float) -> None:
        self._volume = max(0.0, min(1.0, vol))

    def load_voice(self, name: str) -> bool:
        """Selectionne une voix Piper par nom de fichier .onnx (vide = defaut)."""
        tts_dir = MODELS_DIR / "tts"
        target = tts_dir / name if name else models.PIPER_ONNX
        if not target.exists():
            voices = sorted(tts_dir.glob("*.onnx"))
            if not voices:
                return False
            target = voices[0]
        cfg = Path(str(target) + ".json")
        if not cfg.exists():
            return False
        with self._lock:
            self._voice_path = target
            self._config_path = cfg
            self._voice_name = target.name
        return True

    @property
    def voice_name(self) -> str:
        return self._voice_name

    def say(self, text: str) -> None:
        if text.strip():
            self._queue.put(text.strip())

    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            try:
                self._synthesize(text)
            except Exception:
                pass

    def _synthesize(self, text: str) -> None:
        with self._lock:
            voice, cfg = self._voice_path, self._config_path
        if voice is None or cfg is None or not self.ready:
            return
        tmp = Path(tempfile.gettempdir()) / f"cr_tts_{threading.get_ident()}.wav"
        cmd = [
            str(models.PIPER_EXE),
            "-m", str(voice),
            "-c", str(cfg),
            "-f", str(tmp),
        ]
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.run(
            cmd, input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(models.PIPER_DIR), creationflags=flags, timeout=60,
        )
        if not tmp.exists():
            return
        with wave.open(str(tmp), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        tmp.unlink(missing_ok=True)
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        audio *= self._volume
        sd.play(audio, rate)
        sd.wait()
