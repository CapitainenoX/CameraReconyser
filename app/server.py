"""Serveur FastAPI : UI, flux video MJPEG, websocket d'evenements, API config."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, models, updater, version
from .actions import ActionEngine
from .camera import CameraManager
from .face import FaceEngine
from .speaker import SpeakerEngine
from .triggers import TriggerRouter
from .tts import TTSManager, list_voices
from .voice import VoiceManager


class AppState:
    """Conteneur central : modules + diffusion d'evenements vers l'UI."""

    def __init__(self) -> None:
        self.cfg: dict[str, Any] = config.load_config()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[WebSocket] = set()
        self._clients_lock = threading.Lock()

        self.face = FaceEngine()
        self.face.rebuild_from_disk(self.cfg.get("persons", []))
        self.speaker = SpeakerEngine()
        self.speaker.rebuild_from_config(self.cfg.get("speakers", []))
        self.tts = TTSManager()
        self.actions = ActionEngine(self.tts, self.broadcast_threadsafe)
        self.router = TriggerRouter(self.actions, self.face, self.speaker, self.broadcast_threadsafe)
        self.camera = CameraManager(self.face, self._on_camera_event)
        self.voice = VoiceManager(self._on_transcript, self._on_voice_command, self._on_speaker_vec)
        self._enroll_speaker_id: str | None = None

        self.apply_config()

    # ---- diffusion websocket -----------------------------------------------
    async def _send(self, msg: dict[str, Any]) -> None:
        dead = []
        with self._clients_lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        if dead:
            with self._clients_lock:
                for ws in dead:
                    self._clients.discard(ws)

    def broadcast_threadsafe(self, msg: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send(msg), self.loop)

    # ---- callbacks modules --------------------------------------------------
    def _on_camera_event(self, etype: str, data: dict[str, Any]) -> None:
        if etype == "face_recognized":
            self.router.on_face_recognized(data["id"], data["name"], data["score"])
        elif etype == "unknown_face":
            self.router.on_unknown_face(data["score"])
        elif etype == "motion":
            self.router.on_motion(data["pixels"])
        elif etype == "hand_detected":
            self.router.on_hand_detected(data["count"], data["gesture"])
        elif etype == "hand_sequence":
            self.router.on_hand_sequence(data["sequence"])

    def _on_transcript(self, text: str, final: bool) -> None:
        self.broadcast_threadsafe({"event": "transcript", "text": text, "final": final})

    def _on_voice_command(self, phrase: str, rule: dict[str, Any]) -> None:
        self.router.on_voice_command(phrase, rule)

    def _on_speaker_vec(self, vec: list[float], text: str) -> None:
        sid = self._enroll_speaker_id
        if sid:
            self._enroll_speaker_id = None
            ok, msg = self.speaker.add_sample(sid, vec)
            if ok:
                self.save()
            sp = self.speaker.get_speaker(sid)
            self.broadcast_threadsafe({
                "event": "speaker_enrolled", "ok": ok, "message": msg,
                "id": sid, "samples": sp.get("samples", 0) if sp else 0,
            })
            return
        th = float(self.cfg["thresholds"].get("speaker", 0.55))
        sid2, name, score = self.speaker.identify(vec, th)
        self.router.on_speaker_recognized(sid2, name, score, text)

    # ---- application de la config ------------------------------------------
    def apply_config(self) -> None:
        th = self.cfg["thresholds"]
        self.camera.face_threshold = float(th["face"])
        self.camera.motion_threshold = int(th["motion"])
        self.camera.motion_cooldown = float(self.cfg["motion_cooldown_s"])
        self.camera.greeting_debounce = float(self.cfg["greeting_debounce_s"])
        self.camera.hands_enabled = bool(self.cfg.get("hands_enabled", True))
        self.camera.hand_cooldown = float(self.cfg.get("hand_cooldown_s", 5))
        self.router.set_rules(self.cfg["rules"])
        self.router.speaker_greet_debounce = float(self.cfg["greeting_debounce_s"])
        self.speaker.set_speakers(self.cfg.get("speakers", []))
        self.voice.set_rules(self.cfg["rules"], float(th["voice"]))
        self.tts.set_volume(float(self.cfg.get("tts_volume", 1.0)))

    def save(self) -> None:
        self.cfg["persons"] = self.face.export_persons()
        self.cfg["speakers"] = self.speaker.export_speakers()
        config.save_config(self.cfg)
        self.apply_config()


state = AppState()
app = FastAPI(title="Camera Recognizer")


# =============================== UI ========================================
@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(config.web_dir() / "index.html"))


app.mount("/static", StaticFiles(directory=str(config.web_dir())), name="static")


# =============================== Video =====================================
def _mjpeg_gen():
    placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(placeholder, "Camera arretee", (160, 190),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (90, 110, 120), 2, cv2.LINE_AA)
    _, ph = cv2.imencode(".jpg", placeholder)
    ph_bytes = ph.tobytes()
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        frame = state.camera.frame_jpeg() if state.camera.is_running() else None
        payload = frame if frame else ph_bytes
        yield boundary + payload + b"\r\n"
        time.sleep(0.033)


@app.get("/video_feed")
def video_feed() -> StreamingResponse:
    return StreamingResponse(_mjpeg_gen(), media_type="multipart/x-mixed-replace; boundary=frame")


# =============================== WebSocket =================================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    with state._clients_lock:
        state._clients.add(ws)
    await ws.send_text(json.dumps({"event": "status", **_status_payload()}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with state._clients_lock:
            state._clients.discard(ws)


def _status_payload() -> dict[str, Any]:
    return {
        "camera": state.camera.is_running(),
        "voice": state.voice.is_running(),
        "face_ready": state.face.ready,
        "voice_ready": state.voice.ready,
        "speaker_ready": state.voice.spk_ready,
        "tts_ready": state.tts.ready,
        "hands_ready": state.camera.hands_ready,
        "alarm": state.actions.alarm.active,
        "fps": state.camera.fps,
    }


# =============================== API config ===============================
@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return _status_payload()


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    cfg = dict(state.cfg)
    cfg["persons"] = [
        {"id": p["id"], "name": p["name"], "greeting": p.get("greeting", ""),
         "photos": p.get("photos", []), "enrolled": bool(p.get("embedding"))}
        for p in state.face.export_persons()
    ]
    cfg["speakers"] = [_speaker_view(s) for s in state.speaker.export_speakers()]
    return cfg


@app.post("/api/config")
async def post_config(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("camera_index", "mic_index", "tts_voice", "tts_volume", "stt_engine",
                "greeting_debounce_s", "motion_cooldown_s", "thresholds", "rules",
                "hands_enabled", "hand_cooldown_s"):
        if key in payload:
            state.cfg[key] = payload[key]
    if "tts_voice" in payload:
        state.tts.load_voice(payload["tts_voice"])
    state.save()
    return {"ok": True}


# =============================== Devices ==================================
@app.get("/api/cameras")
def cameras() -> dict[str, Any]:
    return {"cameras": CameraManager.list_cameras()}


@app.get("/api/microphones")
def microphones() -> dict[str, Any]:
    return {"microphones": VoiceManager.list_microphones()}


@app.get("/api/voices")
def voices() -> dict[str, Any]:
    return {"voices": list_voices(), "current": state.tts.voice_name}


# =============================== Camera ctrl ==============================
@app.post("/api/camera/start")
def camera_start() -> dict[str, Any]:
    ok, msg = state.camera.start(int(state.cfg.get("camera_index", 0)))
    state.broadcast_threadsafe({"event": "status", **_status_payload()})
    return {"ok": ok, "message": msg}


@app.post("/api/camera/stop")
def camera_stop() -> dict[str, Any]:
    state.camera.stop()
    state.broadcast_threadsafe({"event": "status", **_status_payload()})
    return {"ok": True}


# =============================== Voice ctrl ===============================
@app.post("/api/voice/start")
def voice_start() -> dict[str, Any]:
    mic = state.cfg.get("mic_index")
    ok, msg = state.voice.start(int(mic) if mic is not None else None)
    state.broadcast_threadsafe({"event": "status", **_status_payload()})
    return {"ok": ok, "message": msg}


@app.post("/api/voice/stop")
def voice_stop() -> dict[str, Any]:
    state.voice.stop()
    state.broadcast_threadsafe({"event": "status", **_status_payload()})
    return {"ok": True}


# =============================== Persons ==================================
@app.get("/api/persons")
def persons() -> dict[str, Any]:
    return {"persons": [
        {"id": p["id"], "name": p["name"], "greeting": p.get("greeting", ""),
         "photos": p.get("photos", []), "enrolled": bool(p.get("embedding"))}
        for p in state.face.export_persons()
    ]}


@app.post("/api/persons")
def create_person(payload: dict[str, Any]) -> dict[str, Any]:
    person = state.face.create_person(payload.get("name", ""), payload.get("greeting", ""))
    state.save()
    return {"ok": True, "id": person["id"]}


@app.put("/api/persons/{pid}")
def update_person(pid: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok = state.face.rename_person(pid, payload.get("name", ""), payload.get("greeting"))
    state.save()
    return {"ok": ok}


@app.delete("/api/persons/{pid}")
def delete_person(pid: str) -> dict[str, Any]:
    ok = state.face.delete_person(pid)
    state.save()
    return {"ok": ok}


def _decode_image(raw: bytes) -> np.ndarray | None:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


@app.post("/api/persons/{pid}/photo")
async def add_photo(pid: str, file: UploadFile) -> dict[str, Any]:
    raw = await file.read()
    img = _decode_image(raw)
    if img is None:
        return JSONResponse({"ok": False, "message": "Image illisible."}, status_code=400)
    ok, msg = state.face.add_photo(pid, img)
    if ok:
        state.save()
    return {"ok": ok, "message": msg}


@app.post("/api/persons/{pid}/capture")
def capture_photo(pid: str) -> dict[str, Any]:
    """Capture l'image courante de la camera pour l'enrolement."""
    jpeg = state.camera.frame_jpeg()
    if jpeg is None:
        return {"ok": False, "message": "Demarre la camera d'abord."}
    img = _decode_image(jpeg)
    if img is None:
        return {"ok": False, "message": "Capture impossible."}
    ok, msg = state.face.add_photo(pid, img)
    if ok:
        state.save()
    return {"ok": ok, "message": msg}


# =============================== Speakers =================================
def _speaker_view(s: dict[str, Any]) -> dict[str, Any]:
    return {"id": s["id"], "name": s["name"], "greeting": s.get("greeting", ""),
            "samples": s.get("samples", 0), "enrolled": bool(s.get("embedding"))}


@app.get("/api/speakers")
def speakers() -> dict[str, Any]:
    return {"speakers": [_speaker_view(s) for s in state.speaker.export_speakers()],
            "spk_ready": state.voice.spk_ready, "listening": state.voice.is_running()}


@app.post("/api/speakers")
def create_speaker(payload: dict[str, Any]) -> dict[str, Any]:
    sp = state.speaker.create_speaker(payload.get("name", ""), payload.get("greeting", ""))
    state.save()
    return {"ok": True, "id": sp["id"]}


@app.put("/api/speakers/{sid}")
def update_speaker(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    ok = state.speaker.rename_speaker(sid, payload.get("name", ""), payload.get("greeting"))
    state.save()
    return {"ok": ok}


@app.delete("/api/speakers/{sid}")
def delete_speaker(sid: str) -> dict[str, Any]:
    ok = state.speaker.delete_speaker(sid)
    state.save()
    return {"ok": ok}


@app.post("/api/speakers/{sid}/enroll")
def enroll_speaker(sid: str) -> dict[str, Any]:
    if state.speaker.get_speaker(sid) is None:
        return {"ok": False, "message": "Locuteur introuvable."}
    if not state.voice.spk_ready:
        return {"ok": False, "message": "Modele locuteur absent. Telecharge-le."}
    if not state.voice.is_running():
        return {"ok": False, "message": "Demarre le micro d'abord."}
    state._enroll_speaker_id = sid
    return {"ok": True, "message": "Parle maintenant une phrase claire…"}


@app.post("/api/speakers/enroll/cancel")
def enroll_cancel() -> dict[str, Any]:
    state._enroll_speaker_id = None
    return {"ok": True}


# =============================== Actions ==================================
@app.post("/api/actions/test")
def test_actions(payload: dict[str, Any]) -> dict[str, Any]:
    actions = payload.get("actions", [])
    state.actions.run_actions(actions, {"name": "Test"})
    return {"ok": True}


@app.post("/api/actions/fire/{rule_id}")
def fire_rule(rule_id: str) -> dict[str, Any]:
    return {"ok": state.router.fire_manual(rule_id)}


@app.post("/api/alarm/stop")
def stop_alarm() -> dict[str, Any]:
    state.actions.alarm.stop()
    state.broadcast_threadsafe({"event": "alarm", "active": False})
    return {"ok": True}


# =============================== Updates ==================================
@app.get("/api/version")
def get_version() -> dict[str, Any]:
    return {"version": version.__version__,
            "repo": f"{version.GITHUB_OWNER}/{version.GITHUB_REPO}"}


@app.post("/api/update/check")
def update_check() -> dict[str, Any]:
    return updater.check()


@app.post("/api/update/apply")
def update_apply(payload: dict[str, Any]) -> dict[str, Any]:
    url = (payload or {}).get("download_url", "")

    def progress(pct: float, msg: str) -> None:
        state.broadcast_threadsafe({"event": "update_progress", "pct": round(pct, 1), "message": msg})

    def worker() -> None:
        res = updater.apply(url, progress)
        state.broadcast_threadsafe({"event": "update_done", **res})
        if res.get("ok"):
            time.sleep(1.5)
            os._exit(0)  # ferme l'app pour laisser le script remplacer l'exe

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": "Mise a jour demarree."}


# =============================== Models ===================================
@app.get("/api/models/status")
def models_status() -> dict[str, Any]:
    return {"models": models.status()}


@app.post("/api/models/download")
def models_download(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    keys = (payload or {}).get("keys")

    def progress(key: str, pct: float, msg: str) -> None:
        state.broadcast_threadsafe({"event": "model_progress", "key": key, "pct": round(pct, 1), "message": msg})

    def worker() -> None:
        result = models.download_missing(progress, keys)
        # recharge modules dont le modele vient d'arriver
        state.face.reload_models()
        state.voice.reload_model()
        state.tts.load_voice(state.cfg.get("tts_voice", ""))
        state.broadcast_threadsafe({"event": "models_done", "result": result, **_status_payload()})

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "message": "Telechargement demarre."}
