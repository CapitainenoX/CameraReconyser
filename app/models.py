"""Telechargement et verification des modeles (visage, STT, TTS)."""
from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .config import MODELS_DIR

ProgressCb = Callable[[str, float, str], None]  # (key, pct 0-100, message)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    url: str
    dest: Path           # fichier final attendu
    is_zip: bool = False
    extract_to: Path | None = None
    optional: bool = False


YUNET = MODELS_DIR / "face" / "face_detection_yunet_2023mar.onnx"
SFACE = MODELS_DIR / "face" / "face_recognition_sface_2021dec.onnx"
VOSK_DIR = MODELS_DIR / "stt" / "vosk-model-fr-0.22"
VOSK_SPK_DIR = MODELS_DIR / "stt" / "vosk-model-spk-0.4"
PIPER_ONNX = MODELS_DIR / "tts" / "fr_FR-siwis-medium.onnx"
PIPER_JSON = MODELS_DIR / "tts" / "fr_FR-siwis-medium.onnx.json"
PIPER_GILLES = MODELS_DIR / "tts" / "fr_FR-gilles-low.onnx"
PIPER_GILLES_JSON = MODELS_DIR / "tts" / "fr_FR-gilles-low.onnx.json"
PIPER_UPMC = MODELS_DIR / "tts" / "fr_FR-upmc-medium.onnx"
PIPER_UPMC_JSON = MODELS_DIR / "tts" / "fr_FR-upmc-medium.onnx.json"
PIPER_DIR = MODELS_DIR / "tts" / "piper"
PIPER_EXE = PIPER_DIR / "piper.exe"

SPECS: list[ModelSpec] = [
    ModelSpec(
        "yunet", "Detection de visage (YuNet)",
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        YUNET,
    ),
    ModelSpec(
        "sface", "Reconnaissance faciale (SFace)",
        "https://github.com/opencv/opencv_zoo/raw/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        SFACE,
    ),
    ModelSpec(
        "vosk", "Reconnaissance vocale FR (Vosk - modele complet)",
        "https://alphacephei.com/vosk/models/vosk-model-fr-0.22.zip",
        VOSK_DIR, is_zip=True, extract_to=VOSK_DIR.parent,
    ),
    ModelSpec(
        "piper_onnx", "Voix TTS FR (Piper - modele)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx",
        PIPER_ONNX,
    ),
    ModelSpec(
        "piper_json", "Voix TTS FR (Piper - config)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json",
        PIPER_JSON,
    ),
    ModelSpec(
        "piper_bin", "Moteur TTS Piper (binaire Windows)",
        "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
        "piper_windows_amd64.zip",
        PIPER_DIR, is_zip=True, extract_to=PIPER_DIR.parent,
    ),
    ModelSpec(
        "vosk_spk", "Reconnaissance du locuteur (Vosk x-vector)",
        "https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip",
        VOSK_SPK_DIR, is_zip=True, extract_to=VOSK_SPK_DIR.parent,
    ),
    ModelSpec(
        "piper_gilles", "Voix TTS FR « Gilles » (optionnelle)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx",
        PIPER_GILLES, optional=True,
    ),
    ModelSpec(
        "piper_gilles_json", "Voix TTS FR « Gilles » (config)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx.json",
        PIPER_GILLES_JSON, optional=True,
    ),
    ModelSpec(
        "piper_upmc", "Voix TTS FR « UPMC » (optionnelle)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx",
        PIPER_UPMC, optional=True,
    ),
    ModelSpec(
        "piper_upmc_json", "Voix TTS FR « UPMC » (config)",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx.json",
        PIPER_UPMC_JSON, optional=True,
    ),
]


def spec_by_key(key: str) -> ModelSpec | None:
    return next((s for s in SPECS if s.key == key), None)


def is_present(spec: ModelSpec) -> bool:
    if spec.is_zip:
        return spec.dest.exists() and any(spec.dest.iterdir())
    return spec.dest.exists() and spec.dest.stat().st_size > 0


def status() -> dict[str, dict]:
    """Etat de chaque modele pour l'UI."""
    return {
        s.key: {"label": s.label, "present": is_present(s), "url": s.url,
                "optional": s.optional}
        for s in SPECS
    }


def all_present(keys: list[str] | None = None) -> bool:
    specs = [s for s in SPECS if not s.optional] if keys is None else [s for s in SPECS if s.key in keys]
    return all(is_present(s) for s in specs)


def _download_file(url: str, dest: Path, key: str, cb: ProgressCb) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                pct = (done / total * 100) if total else 0.0
                cb(key, pct, "telechargement")
    tmp.replace(dest)


def download_one(spec: ModelSpec, cb: ProgressCb) -> None:
    cb(spec.key, 0.0, "demarrage")
    if spec.is_zip:
        assert spec.extract_to is not None
        zip_path = spec.extract_to / f"{spec.key}.zip"
        _download_file(spec.url, zip_path, spec.key, cb)
        cb(spec.key, 99.0, "extraction")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(spec.extract_to)
        zip_path.unlink(missing_ok=True)
    else:
        _download_file(spec.url, spec.dest, spec.key, cb)
    cb(spec.key, 100.0, "termine")


def download_missing(cb: ProgressCb, keys: list[str] | None = None) -> dict[str, str]:
    """Telecharge les modeles manquants. Retourne {key: 'ok'|message d'erreur}."""
    result: dict[str, str] = {}
    targets = SPECS if keys is None else [s for s in SPECS if s.key in keys]
    for spec in targets:
        if is_present(spec):
            result[spec.key] = "ok"
            continue
        try:
            download_one(spec, cb)
            result[spec.key] = "ok"
        except Exception as exc:  # reseau absent, 404, etc.
            cb(spec.key, 0.0, f"echec: {exc}")
            result[spec.key] = f"echec: {exc}"
    return result


def ensure_default_alarm(sounds_dir: Path) -> Path:
    """Genere un WAV d'alarme par defaut si absent (bip module 880/440 Hz)."""
    import math
    import struct
    import wave

    path = sounds_dir / "alarm.wav"
    if path.exists():
        return path
    rate = 44100
    dur = 1.0
    frames = bytearray()
    for i in range(int(rate * dur)):
        t = i / rate
        freq = 880 if (t % 0.5) < 0.25 else 440
        sample = int(0.6 * 32767 * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path
