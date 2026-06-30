"""Gestion du dossier de donnees portable et de la configuration JSON."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()


def _exe_dir() -> Path:
    """Dossier de l'executable (ou du projet en mode source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        return True
    except Exception:
        return False


def resolve_data_dir() -> Path:
    """./data a cote de l'exe si inscriptible, sinon %LOCALAPPDATA%."""
    portable = _exe_dir() / "data"
    if _writable(portable):
        return portable
    fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CameraRecognizer"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DATA_DIR = resolve_data_dir()
FACES_DIR = DATA_DIR / "faces"
MODELS_DIR = DATA_DIR / "models"
SOUNDS_DIR = DATA_DIR / "sounds"
CONFIG_PATH = DATA_DIR / "config.json"

for _d in (FACES_DIR, MODELS_DIR, SOUNDS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def web_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"  # type: ignore[attr-defined]
    return _exe_dir() / "web"


DEFAULT_CONFIG: dict[str, Any] = {
    "camera_index": 0,
    "mic_index": None,
    "tts_voice": "",
    "tts_volume": 1.0,
    "stt_engine": "vosk",   # "vosk" (local) ou "parakeet" (GPU/source)
    "thresholds": {
        "face": 0.36,        # similarite cosinus SFace (match si >=)
        "voice": 72.0,       # score rapidfuzz 0-100
        "motion": 1500,      # nb de pixels en mouvement
        "speaker": 0.55,     # similarite cosinus x-vector Vosk (match si >=)
    },
    "greeting_debounce_s": 30,
    "motion_cooldown_s": 8,
    "hands_enabled": True,
    "hand_cooldown_s": 5,
    "rules": [
        {
            "id": "rule_demo_salut",
            "name": "Salut vocal",
            "enabled": True,
            "trigger": {"type": "voice_command", "phrase": "salut"},
            "actions": [{"type": "say", "text": "Salut !"}],
        },
        {
            "id": "rule_demo_alarme",
            "name": "Alarme vocale",
            "enabled": True,
            "trigger": {"type": "voice_command", "phrase": "declenche l'alarme"},
            "actions": [{"type": "alarm"}],
        },
        {
            "id": "rule_stop_alarme",
            "name": "Stop alarme vocal",
            "enabled": True,
            "trigger": {"type": "voice_command", "phrase": "arrete l'alarme"},
            "actions": [{"type": "stop_alarm"}],
        },
    ],
    "persons": [],   # rempli par face.py (id, name, greeting, embedding, photos)
    "speakers": [],  # rempli par speaker.py (id, name, greeting, embedding, samples)
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config() -> dict[str, Any]:
    with _LOCK:
        if not CONFIG_PATH.exists():
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(DEFAULT_CONFIG))
        return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict[str, Any]) -> None:
    """Ecriture atomique pour eviter une config corrompue."""
    with _LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
