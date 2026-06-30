"""Boucle camera OpenCV : capture, detection visage, mains, mouvement, annotation."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

from .face import FaceEngine
from .hands import HandTracker

EventCb = Callable[[str, dict[str, Any]], None]

# Palette HUD (BGR) coherente avec l'UI night-vision
_AMBER = (71, 179, 255)   # #FFB347
_CYAN = (213, 196, 92)    # #5CC4D5
_GREEN = (140, 208, 111)  # #6FD08C
_GREY = (120, 120, 120)
_HAND_CONN = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
    (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]


class CameraManager:
    def __init__(self, face: FaceEngine, emit: EventCb) -> None:
        self.face = face
        self.emit = emit
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._frame_jpeg: bytes | None = None
        self._index = 0

        # parametres regles par l'UI
        self.face_threshold = 0.36
        self.motion_threshold = 1500
        self.motion_cooldown = 8.0
        self.greeting_debounce = 30.0
        self.hands_enabled = True
        self.hand_cooldown = 5.0

        # modules
        self.hands = HandTracker()
        self._mog2 = cv2.createBackgroundSubtractorMOG2(history=400, varThreshold=32, detectShadows=False)

        # etat
        self._last_motion_emit = 0.0
        self._last_hand_emit = 0.0
        self._hand_streak_gesture = ""
        self._hand_streak = 0
        self._hand_stable_needed = 3  # frames stables avant declenchement
        self._gesture_seq: list[str] = []         # suite de gestes stables distincts
        self._gesture_seq_last = 0.0              # ts du dernier ajout
        self.gesture_seq_window = 4.0             # reset si inactif plus longtemps
        self._last_seen: dict[str, float] = {}   # person_id -> ts
        self._recognize_every = 2                 # throttle SEULEMENT l'embedding
        self.fps = 0.0

    # ---- cycle de vie -------------------------------------------------------
    def is_running(self) -> bool:
        return self._running

    @property
    def hands_ready(self) -> bool:
        return self.hands.ready

    def start(self, index: int) -> tuple[bool, str]:
        self.stop()
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            return False, f"Impossible d'ouvrir la camera {index} (occupee ?)."
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self._cap = cap
        self._index = index
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True, "Camera demarree."

    def stop(self) -> None:
        self._running = False
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._frame_jpeg = None

    # ---- boucle -------------------------------------------------------------
    def _loop(self) -> None:
        frame_count = 0
        t_fps = time.time()
        names: dict[int, dict[str, Any]] = {}  # cache nom par index de visage
        hands_cache: list[dict[str, Any]] = []
        while self._running and self._cap is not None:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame_count += 1
            now = time.time()

            motion_px = self._detect_motion(frame)
            if motion_px >= self.motion_threshold and (now - self._last_motion_emit) >= self.motion_cooldown:
                self._last_motion_emit = now
                self.emit("motion", {"pixels": int(motion_px)})

            # visages : detection CHAQUE frame (boites fluides), reco throttlee
            faces = self.face.detect(frame) if self.face.detector is not None else []
            do_reco = (frame_count % self._recognize_every == 0)
            face_info = self._resolve_faces(frame, faces, now, names, do_reco)

            # mains : traitement throttle, cache pour dessin fluide
            if self.hands_enabled and self.hands.ready and frame_count % 2 == 0:
                hands_cache = self.hands.process(frame)
                gesture = hands_cache[0]["gesture"] if hands_cache else ""
                # exige un geste stable sur plusieurs frames -> moins de faux declenchements
                if gesture and gesture == self._hand_streak_gesture:
                    self._hand_streak += 1
                else:
                    self._hand_streak_gesture = gesture
                    self._hand_streak = 1 if gesture else 0
                if self._hand_streak == self._hand_stable_needed:
                    if (now - self._last_hand_emit) >= self.hand_cooldown:
                        self._last_hand_emit = now
                        self.emit("hand_detected", {
                            "count": len(hands_cache),
                            "gesture": gesture,
                        })
                    self._push_gesture(gesture, now)

            annotated = self._annotate(frame, face_info, hands_cache, motion_px)

            if frame_count % 10 == 0:
                dt = now - t_fps
                self.fps = round(10 / dt, 1) if dt > 0 else 0.0
                t_fps = now

            ok2, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                with self._lock:
                    self._frame_jpeg = buf.tobytes()
            time.sleep(0.003)

    def _push_gesture(self, gesture: str, now: float) -> None:
        """Empile un geste stable distinct -> emet la suite courante."""
        if not gesture:
            return
        if now - self._gesture_seq_last > self.gesture_seq_window:
            self._gesture_seq = []
        self._gesture_seq_last = now
        if self._gesture_seq and self._gesture_seq[-1] == gesture:
            return  # meme geste maintenu, pas un nouveau pas
        self._gesture_seq.append(gesture)
        self._gesture_seq = self._gesture_seq[-6:]
        self.emit("hand_sequence", {"sequence": list(self._gesture_seq)})

    def _detect_motion(self, frame: np.ndarray) -> int:
        mask = self._mog2.apply(frame)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.medianBlur(mask, 5)
        return int(cv2.countNonZero(mask))

    def _resolve_faces(self, frame, faces, now, names, do_reco) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for idx, row in enumerate(faces):
            x, y, w, h = (int(v) for v in row[:4])
            if do_reco and self.face.recognizer is not None:
                emb = self.face.embedding(frame, row)
                pid, name, score = ("", "Inconnu", 0.0)
                if emb is not None:
                    pid, name, score = self.face.recognize(emb, self.face_threshold)
                names[idx] = {"id": pid, "name": name, "score": round(score, 3)}
                if pid:
                    last = self._last_seen.get(pid, 0.0)
                    if (now - last) >= self.greeting_debounce:
                        self.emit("face_recognized", {"id": pid, "name": name, "score": round(score, 3)})
                    self._last_seen[pid] = now
                elif name == "Inconnu" and w * h > 2500:
                    self.emit("unknown_face", {"score": round(score, 3)})
            cached = names.get(idx, {"id": "", "name": "...", "score": 0.0})
            results.append({"box": (x, y, w, h), **cached})
        # purge cache des index disparus
        for k in [k for k in names if k >= len(faces)]:
            names.pop(k, None)
        return results

    def _annotate(self, frame, faces, hands_list, motion_px) -> np.ndarray:
        out = frame
        for f in faces:
            x, y, w, h = f["box"]
            known = bool(f["id"])
            color = _AMBER if known else _GREY
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
            ln = max(12, w // 6)
            for (cx, cy, dx, dy) in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
                cv2.line(out, (cx, cy), (cx + dx * ln, cy), _CYAN, 2)
                cv2.line(out, (cx, cy), (cx, cy + dy * ln), _CYAN, 2)
            label = f["name"] if not known else f"{f['name']}  {f['score']:.2f}"
            cv2.rectangle(out, (x, y - 22), (x + max(80, len(label) * 11), y), color, -1)
            cv2.putText(out, label, (x + 4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (10, 14, 15), 1, cv2.LINE_AA)
        for hnd in hands_list:
            pts = hnd["points"]
            for a, b in _HAND_CONN:
                cv2.line(out, pts[a], pts[b], _GREEN, 2)
            for p in pts:
                cv2.circle(out, p, 3, _CYAN, -1)
            hx, hy, _, _ = hnd["box"]
            tag = f"{hnd['label']} · {hnd['gesture']}"
            cv2.putText(out, tag, (hx, max(16, hy - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, _GREEN, 2, cv2.LINE_AA)
        if motion_px >= self.motion_threshold:
            cv2.putText(out, "MOUVEMENT", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, _CYAN, 2, cv2.LINE_AA)
        return out

    def frame_jpeg(self) -> bytes | None:
        with self._lock:
            return self._frame_jpeg

    @staticmethod
    def list_cameras(max_index: int = 6) -> list[int]:
        found: list[int] = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                found.append(i)
            cap.release()
        return found
