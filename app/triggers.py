"""Routeur declencheur -> actions. Relie les evenements aux regles."""
from __future__ import annotations

import time
from typing import Any, Callable

from .actions import ActionEngine
from .face import FaceEngine
from .speaker import SpeakerEngine

BroadcastCb = Callable[[dict[str, Any]], None]


class TriggerRouter:
    def __init__(self, actions: ActionEngine, face: FaceEngine,
                 speaker: SpeakerEngine, broadcast: BroadcastCb) -> None:
        self.actions = actions
        self.face = face
        self.speaker = speaker
        self.broadcast = broadcast
        self._rules: list[dict[str, Any]] = []
        self.speaker_greet_debounce = 30.0
        self._spk_last: dict[str, float] = {}
        self._seq_last: dict[str, float] = {}
        self.seq_cooldown = 2.0

    def set_rules(self, rules: list[dict[str, Any]]) -> None:
        self._rules = rules

    def _enabled(self, ttype: str) -> list[dict[str, Any]]:
        return [
            r for r in self._rules
            if r.get("enabled", True) and r.get("trigger", {}).get("type") == ttype
        ]

    # ---- evenements ---------------------------------------------------------
    def on_face_recognized(self, pid: str, name: str, score: float) -> None:
        self.broadcast({"event": "face", "id": pid, "name": name, "score": score})
        ctx = {"name": name, "id": pid}
        # 1) salutation personnalisee de la personne
        person = self.face.get_person(pid)
        if person and person.get("greeting"):
            self.actions.run_actions([{"type": "say", "text": person["greeting"]}], ctx)
        elif person:
            self.actions.run_actions([{"type": "say", "text": f"Bonjour {name}"}], ctx)
        # 2) regles face_recognized (personne precise ou "toute personne connue")
        for rule in self._enabled("face_recognized"):
            target = rule["trigger"].get("person", "any_known")
            if target in ("any_known", pid):
                self.actions.run_actions(rule["actions"], ctx)

    def on_unknown_face(self, score: float) -> None:
        self.broadcast({"event": "face", "id": "", "name": "Inconnu", "score": score})
        for rule in self._enabled("face_recognized"):
            if rule["trigger"].get("person") == "unknown":
                self.actions.run_actions(rule["actions"], {"name": "Inconnu"})

    def on_motion(self, pixels: int) -> None:
        self.broadcast({"event": "motion", "pixels": pixels})
        for rule in self._enabled("motion_detected"):
            self.actions.run_actions(rule["actions"], {})

    def on_hand_detected(self, count: int, gesture: str) -> None:
        self.broadcast({"event": "hand", "count": count, "gesture": gesture})
        for rule in self._enabled("hand_detected"):
            want = rule["trigger"].get("gesture", "any")
            if want in ("any", gesture):
                self.actions.run_actions(rule["actions"], {"gesture": gesture})

    def on_hand_sequence(self, sequence: list[str]) -> None:
        self.broadcast({"event": "hand_sequence", "sequence": sequence})
        now = time.time()
        for rule in self._enabled("hand_sequence"):
            want = [g.strip() for g in rule["trigger"].get("sequence", []) if g.strip()]
            n = len(want)
            if n and sequence[-n:] == want:
                if now - self._seq_last.get(rule.get("id", ""), 0.0) >= self.seq_cooldown:
                    self._seq_last[rule.get("id", "")] = now
                    self.actions.run_actions(rule["actions"], {"sequence": " > ".join(sequence)})

    def on_speaker_recognized(self, sid: str, name: str, score: float, text: str) -> None:
        self.broadcast({"event": "speaker", "id": sid, "name": name,
                        "score": round(score, 3), "text": text})
        ctx = {"name": name, "id": sid, "text": text}
        if sid:
            now = time.time()
            if now - self._spk_last.get(sid, 0.0) >= self.speaker_greet_debounce:
                self._spk_last[sid] = now
                speaker = self.speaker.get_speaker(sid)
                if speaker and speaker.get("greeting"):
                    self.actions.run_actions([{"type": "say", "text": speaker["greeting"]}], ctx)
                elif speaker:
                    self.actions.run_actions([{"type": "say", "text": f"Bonjour {name}"}], ctx)
        for rule in self._enabled("speaker_recognized"):
            target = rule["trigger"].get("speaker", "any_known")
            if target == "unknown" and not sid:
                self.actions.run_actions(rule["actions"], ctx)
            elif sid and target in ("any_known", sid):
                self.actions.run_actions(rule["actions"], ctx)

    def on_voice_command(self, phrase: str, rule: dict[str, Any]) -> None:
        self.broadcast({"event": "command", "phrase": phrase, "rule": rule.get("name", "")})
        self.actions.run_actions(rule["actions"], {})

    def fire_startup(self) -> None:
        for rule in self._enabled("startup"):
            self.actions.run_actions(rule["actions"], {})

    def fire_manual(self, rule_id: str) -> bool:
        rule = next((r for r in self._rules if r.get("id") == rule_id), None)
        if not rule:
            return False
        self.actions.run_actions(rule["actions"], {})
        return True
