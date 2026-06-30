"""Enrolement, embeddings SFace et reconnaissance faciale locale."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import models
from .config import FACES_DIR


class FaceEngine:
    """Detection (YuNet) + reconnaissance (SFace). Tout en local."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.detector: cv2.FaceDetectorYN | None = None
        self.recognizer: cv2.FaceRecognizerSF | None = None
        self._input_size = (320, 320)
        self.persons: list[dict[str, Any]] = []  # {id,name,greeting,embedding}
        self._load_models()

    # ---- chargement modeles -------------------------------------------------
    def _load_models(self) -> None:
        with self._lock:
            if models.is_present(models.SPECS[0]) and self.detector is None:
                self.detector = cv2.FaceDetectorYN.create(
                    str(models.YUNET), "", self._input_size,
                    score_threshold=0.7, nms_threshold=0.3, top_k=50,
                )
            if models.is_present(models.SPECS[1]) and self.recognizer is None:
                self.recognizer = cv2.FaceRecognizerSF.create(str(models.SFACE), "")

    def reload_models(self) -> None:
        self.detector = None
        self.recognizer = None
        self._load_models()

    @property
    def ready(self) -> bool:
        return self.detector is not None and self.recognizer is not None

    # ---- detection / reconnaissance ----------------------------------------
    def detect(self, frame: np.ndarray) -> np.ndarray:
        """Retourne un tableau Nx15 (bbox + landmarks + score) ou vide."""
        if self.detector is None:
            return np.empty((0, 15), dtype=np.float32)
        h, w = frame.shape[:2]
        if (w, h) != self._input_size:
            self._input_size = (w, h)
            self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame)
        return faces if faces is not None else np.empty((0, 15), dtype=np.float32)

    def embedding(self, frame: np.ndarray, face_row: np.ndarray) -> np.ndarray | None:
        if self.recognizer is None:
            return None
        aligned = self.recognizer.alignCrop(frame, face_row)
        feat = self.recognizer.feature(aligned)
        return feat.flatten().astype(np.float32)

    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom else 0.0

    def recognize(self, emb: np.ndarray, threshold: float) -> tuple[str, str, float]:
        """Retourne (person_id, name, score). id vide si inconnu."""
        best_id, best_name, best_score = "", "Inconnu", 0.0
        with self._lock:
            for p in self.persons:
                ref = np.asarray(p["embedding"], dtype=np.float32)
                score = self.cosine(emb, ref)
                if score > best_score:
                    best_id, best_name, best_score = p["id"], p["name"], score
        if best_score >= threshold:
            return best_id, best_name, best_score
        return "", "Inconnu", best_score

    # ---- gestion des personnes ---------------------------------------------
    def set_persons(self, persons: list[dict[str, Any]]) -> None:
        with self._lock:
            self.persons = [p for p in persons if p.get("embedding")]

    def export_persons(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self.persons]

    def _person_dir(self, pid: str) -> Path:
        d = FACES_DIR / pid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def create_person(self, name: str, greeting: str = "") -> dict[str, Any]:
        pid = "p_" + uuid.uuid4().hex[:10]
        person = {
            "id": pid,
            "name": name.strip() or pid,
            "greeting": greeting.strip(),
            "embedding": [],
            "photos": [],
        }
        with self._lock:
            self.persons.append(person)
        self._person_dir(pid)
        return person

    def get_person(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            return next((p for p in self.persons if p["id"] == pid), None)

    def rename_person(self, pid: str, name: str, greeting: str | None) -> bool:
        with self._lock:
            p = next((p for p in self.persons if p["id"] == pid), None)
            if not p:
                return False
            if name.strip():
                p["name"] = name.strip()
            if greeting is not None:
                p["greeting"] = greeting.strip()
        return True

    def delete_person(self, pid: str) -> bool:
        with self._lock:
            before = len(self.persons)
            self.persons = [p for p in self.persons if p["id"] != pid]
            changed = len(self.persons) != before
        d = FACES_DIR / pid
        if d.exists():
            for f in d.glob("*"):
                f.unlink(missing_ok=True)
            d.rmdir()
        return changed

    def add_photo(self, pid: str, image_bgr: np.ndarray) -> tuple[bool, str]:
        """Ajoute une photo, recalcule l'embedding moyen de la personne."""
        if not self.ready:
            return False, "Modeles de visage absents."
        faces = self.detect(image_bgr)
        if len(faces) == 0:
            return False, "Aucun visage detecte sur l'image."
        # plus grande boite = visage principal
        row = max(faces, key=lambda f: f[2] * f[3])
        emb = self.embedding(image_bgr, row)
        if emb is None:
            return False, "Echec du calcul de l'embedding."
        pdir = self._person_dir(pid)
        idx = len(list(pdir.glob("photo_*.jpg")))
        photo_path = pdir / f"photo_{idx:03d}.jpg"
        cv2.imwrite(str(photo_path), image_bgr)
        with self._lock:
            p = next((p for p in self.persons if p["id"] == pid), None)
            if not p:
                return False, "Personne introuvable."
            p.setdefault("photos", []).append(photo_path.name)
            embs = p.get("_embs", [])
            embs.append(emb.tolist())
            p["_embs"] = embs
            mean = np.mean(np.asarray(embs, dtype=np.float32), axis=0)
            p["embedding"] = mean.tolist()
        return True, "Photo ajoutee."

    def rebuild_from_disk(self, persons_cfg: list[dict[str, Any]]) -> None:
        """Recharge embeddings depuis la config (deja calcules)."""
        self.set_persons(persons_cfg)
