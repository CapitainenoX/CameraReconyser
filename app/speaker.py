"""Enrolement et identification du locuteur via x-vectors Vosk (100% local)."""
from __future__ import annotations

import threading
import uuid
from typing import Any

import numpy as np


class SpeakerEngine:
    """Stocke un empreinte vocale moyenne par locuteur, identifie par cosinus."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.speakers: list[dict[str, Any]] = []  # {id,name,greeting,embedding,samples,_embs}

    # ---- comparaison --------------------------------------------------------
    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom else 0.0

    def identify(self, vec: list[float], threshold: float) -> tuple[str, str, float]:
        """Retourne (speaker_id, name, score). id vide si inconnu."""
        emb = np.asarray(vec, dtype=np.float32)
        best_id, best_name, best_score = "", "Inconnu", 0.0
        with self._lock:
            for s in self.speakers:
                ref = np.asarray(s["embedding"], dtype=np.float32)
                if ref.shape != emb.shape:
                    continue
                score = self.cosine(emb, ref)
                if score > best_score:
                    best_id, best_name, best_score = s["id"], s["name"], score
        if best_id and best_score >= threshold:
            return best_id, best_name, best_score
        return "", "Inconnu", best_score

    # ---- gestion des locuteurs ---------------------------------------------
    def set_speakers(self, speakers: list[dict[str, Any]]) -> None:
        with self._lock:
            self.speakers = [s for s in speakers if s.get("embedding")]

    def export_speakers(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(s) for s in self.speakers]

    def create_speaker(self, name: str, greeting: str = "") -> dict[str, Any]:
        sid = "s_" + uuid.uuid4().hex[:10]
        speaker = {
            "id": sid,
            "name": name.strip() or sid,
            "greeting": greeting.strip(),
            "embedding": [],
            "samples": 0,
        }
        with self._lock:
            self.speakers.append(speaker)
        return speaker

    def get_speaker(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            return next((s for s in self.speakers if s["id"] == sid), None)

    def rename_speaker(self, sid: str, name: str, greeting: str | None) -> bool:
        with self._lock:
            s = next((s for s in self.speakers if s["id"] == sid), None)
            if not s:
                return False
            if name.strip():
                s["name"] = name.strip()
            if greeting is not None:
                s["greeting"] = greeting.strip()
        return True

    def delete_speaker(self, sid: str) -> bool:
        with self._lock:
            before = len(self.speakers)
            self.speakers = [s for s in self.speakers if s["id"] != sid]
            return len(self.speakers) != before

    def add_sample(self, sid: str, vec: list[float]) -> tuple[bool, str]:
        """Ajoute une empreinte vocale, recalcule l'embedding moyen."""
        with self._lock:
            s = next((s for s in self.speakers if s["id"] == sid), None)
            if not s:
                return False, "Locuteur introuvable."
            embs = s.get("_embs", [])
            embs.append([float(x) for x in vec])
            s["_embs"] = embs
            mean = np.mean(np.asarray(embs, dtype=np.float32), axis=0)
            s["embedding"] = mean.tolist()
            s["samples"] = len(embs)
        return True, "Echantillon vocal ajoute."

    def rebuild_from_config(self, speakers_cfg: list[dict[str, Any]]) -> None:
        self.set_speakers(speakers_cfg)
