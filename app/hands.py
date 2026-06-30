"""Detection de mains via MediaPipe Hands (import optionnel)."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    import mediapipe as mp
    _MP_OK = True
except Exception:  # mediapipe absent -> module desactive proprement
    _MP_OK = False


class HandTracker:
    """Detecte les mains et estime un geste simple (poing/main ouverte)."""

    def __init__(self) -> None:
        self._hands = None
        if _MP_OK:
            self._hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=2,
                model_complexity=1,           # +precis sur le nombre de doigts
                min_detection_confidence=0.7,  # moins sensible (moins de faux positifs)
                min_tracking_confidence=0.6,
            )

    @property
    def ready(self) -> bool:
        return self._hands is not None

    def process(self, frame_bgr: np.ndarray) -> list[dict[str, Any]]:
        """Retourne une liste de mains : {box, gesture, label}. Vide si rien."""
        if self._hands is None:
            return []
        import cv2
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        res = self._hands.process(rgb)
        out: list[dict[str, Any]] = []
        if not res.multi_hand_landmarks:
            return out
        handed = res.multi_handedness or []
        for i, lms in enumerate(res.multi_hand_landmarks):
            xs = [p.x for p in lms.landmark]
            ys = [p.y for p in lms.landmark]
            x1, y1 = int(min(xs) * w), int(min(ys) * h)
            x2, y2 = int(max(xs) * w), int(max(ys) * h)
            label = "Main"
            if i < len(handed) and handed[i].classification:
                label = "Droite" if handed[i].classification[0].label == "Right" else "Gauche"
            out.append({
                "box": (x1, y1, x2 - x1, y2 - y1),
                "gesture": self._gesture(lms.landmark),
                "label": label,
                "points": [(int(p.x * w), int(p.y * h)) for p in lms.landmark],
            })
        return out

    @staticmethod
    def _gesture(lm) -> str:
        """Compte les doigts leves -> poing / main ouverte / nombre.

        Methode robuste a la rotation : un doigt est tendu si sa pointe est
        nettement plus loin du poignet que son articulation (PIP), avec une
        marge relative a la taille de la main pour eviter le bruit.
        """
        def d(a, b) -> float:
            return ((lm[a].x - lm[b].x) ** 2 + (lm[a].y - lm[b].y) ** 2) ** 0.5

        scale = d(0, 9) or 1e-6          # poignet -> base du majeur
        margin = 0.45 * scale            # filtre les doigts a moitie plies
        up = 0
        for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
            if d(tip, 0) - d(pip, 0) > margin:
                up += 1
        # pouce : pointe (4) vs articulation MCP (2), distance au poignet
        if d(4, 0) - d(2, 0) > margin:
            up += 1
        if up == 0:
            return "poing"
        if up >= 5:
            return "main ouverte"
        return f"{up} doigts"

    def close(self) -> None:
        if self._hands is not None:
            self._hands.close()
            self._hands = None
