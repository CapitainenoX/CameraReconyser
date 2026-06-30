"""Reconnaissance vocale Vosk en streaming + matching de commandes (rapidfuzz)."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable

import sounddevice as sd
from rapidfuzz import fuzz

from . import models

TranscriptCb = Callable[[str, bool], None]            # (texte, final?)
CommandCb = Callable[[str, dict[str, Any]], None]     # (phrase, rule)
SpeakerCb = Callable[[list[float], str], None]        # (x-vector, texte)

# Lexique de synonymes FR : mots ramenes a un jeton canonique pour que des
# formulations equivalentes declenchent la meme commande (matching semantique
# leger, 100% local). "lance" == "ouvre" == "demarre", etc.
_SYNONYMS: dict[str, str] = {
    "ouvre": "ouvrir", "ouvrir": "ouvrir", "lance": "ouvrir", "lancer": "ouvrir",
    "demarre": "ouvrir", "demarrer": "ouvrir", "active": "ouvrir", "activer": "ouvrir",
    "mets": "ouvrir", "metz": "ouvrir", "affiche": "ouvrir", "demarrez": "ouvrir",
    "ferme": "fermer", "fermer": "fermer", "quitte": "fermer", "quitter": "fermer",
    "ferme-moi": "fermer",
    "arrete": "arreter", "arreter": "arreter", "stop": "arreter", "stoppe": "arreter",
    "coupe": "arreter", "couper": "arreter", "eteins": "arreter", "desactive": "arreter",
    "desactiver": "arreter", "annule": "arreter",
    "declenche": "declencher", "declencher": "declencher", "active-l": "declencher",
    "sonne": "declencher", "alerte": "alarme", "alarme": "alarme",
    "salut": "bonjour", "coucou": "bonjour", "bonjour": "bonjour", "hello": "bonjour",
    "hey": "bonjour", "yo": "bonjour", "bonsoir": "bonjour",
    "musique": "musique", "son": "musique", "chanson": "musique",
    "lumiere": "lumiere", "lumieres": "lumiere", "lampe": "lumiere",
}


class VoiceManager:
    def __init__(
        self,
        on_transcript: TranscriptCb,
        on_command: CommandCb,
        on_speaker: SpeakerCb | None = None,
    ) -> None:
        self.on_transcript = on_transcript
        self.on_command = on_command
        self.on_speaker = on_speaker
        self._model = None
        self._spk_model = None
        self._stream: sd.RawInputStream | None = None
        self._rec = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._audio_q: queue.Queue[bytes] = queue.Queue()
        self._rules: list[dict[str, Any]] = []
        self._threshold = 78.0
        self._samplerate = 16000
        self._grammar: str | None = None
        self._load_model()

    def _load_model(self) -> None:
        if self._model is None and models.is_present(models.SPECS[2]):
            try:
                from vosk import Model
                self._model = Model(str(models.VOSK_DIR))
            except Exception:
                self._model = None
        self._load_spk()

    def _load_spk(self) -> None:
        spec = models.spec_by_key("vosk_spk")
        if self._spk_model is None and spec is not None and models.is_present(spec):
            try:
                from vosk import SpkModel
                self._spk_model = SpkModel(str(models.VOSK_SPK_DIR))
            except Exception:
                self._spk_model = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def spk_ready(self) -> bool:
        return self._spk_model is not None

    def reload_model(self) -> None:
        self._model = None
        self._spk_model = None
        self._load_model()

    def set_rules(self, rules: list[dict[str, Any]], threshold: float) -> None:
        self._rules = [
            r for r in rules
            if r.get("enabled", True) and r.get("trigger", {}).get("type") == "voice_command"
        ]
        self._threshold = threshold
        self._build_grammar()
        if self._running:
            self._restart_recognizer()

    @staticmethod
    def _phrases(rule: dict[str, Any]) -> list[str]:
        """Phrase principale + formulations equivalentes (synonyms)."""
        trig = rule.get("trigger", {})
        out = [trig.get("phrase", "")]
        out += [str(s) for s in trig.get("synonyms", []) if str(s).strip()]
        return [p for p in out if p.strip()]

    @staticmethod
    def _normalize(text: str) -> str:
        """Remplace chaque mot par son jeton canonique (synonymes)."""
        toks = text.lower().replace("'", " ").split()
        return " ".join(_SYNONYMS.get(t, t) for t in toks)

    def _build_grammar(self) -> None:
        """Grammaire Vosk = mots des phrases + synonymes -> reco bien meilleure."""
        words: set[str] = set()
        for r in self._rules:
            for phrase in self._phrases(r):
                for w in phrase.lower().replace("'", " ").split():
                    if w.isalpha():
                        words.add(w)
        # ajoute tous les synonymes connus pour qu'ils soient reconnaissables
        words.update(w for w in _SYNONYMS if w.isalpha())
        if words:
            self._grammar = json.dumps(sorted(words) + ["[unk]"], ensure_ascii=False)
        else:
            self._grammar = None

    def _make_recognizer(self):
        from vosk import KaldiRecognizer
        if self._grammar:
            rec = KaldiRecognizer(self._model, self._samplerate, self._grammar)
        else:
            rec = KaldiRecognizer(self._model, self._samplerate)
        if self._spk_model is not None:
            try:
                rec.SetSpkModel(self._spk_model)
            except Exception:
                pass
        return rec

    def _restart_recognizer(self) -> None:
        try:
            self._rec = self._make_recognizer()
        except Exception:
            pass

    def is_running(self) -> bool:
        return self._running

    def start(self, mic_index: int | None) -> tuple[bool, str]:
        self._load_model()
        if self._model is None:
            return False, "Modele Vosk absent."
        self.stop()
        try:
            self._rec = self._make_recognizer()
            self._stream = sd.RawInputStream(
                samplerate=self._samplerate, blocksize=8000,
                device=mic_index, dtype="int16", channels=1,
                callback=self._audio_cb,
            )
            self._stream.start()
        except Exception as exc:
            return False, f"Micro indisponible: {exc}"
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True, "Ecoute vocale demarree."

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.5)
        with self._audio_q.mutex:
            self._audio_q.queue.clear()

    def _audio_cb(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self._audio_q.put(bytes(indata))

    def _loop(self) -> None:
        while self._running and self._rec is not None:
            try:
                data = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._rec.AcceptWaveform(data):
                res = json.loads(self._rec.Result())
                text = res.get("text", "").strip()
                if text:
                    self.on_transcript(text, True)
                    self._match(text)
                vec = res.get("spk")
                if vec and self.on_speaker and (text or res.get("spk_frames", 0) > 40):
                    self.on_speaker(vec, text)
            else:
                partial = json.loads(self._rec.PartialResult()).get("partial", "").strip()
                if partial:
                    self.on_transcript(partial, False)

    def _match(self, text: str) -> None:
        spoken = text.lower()
        spoken_norm = self._normalize(text)
        best_rule = None
        best_score = 0.0
        for rule in self._rules:
            for phrase in self._phrases(rule):
                p = phrase.strip().lower()
                if not p:
                    continue
                p_norm = self._normalize(phrase)
                score = max(
                    fuzz.partial_ratio(p, spoken),
                    fuzz.token_set_ratio(p, spoken),
                    fuzz.partial_ratio(p_norm, spoken_norm),
                    fuzz.token_set_ratio(p_norm, spoken_norm),
                )
                if score > best_score:
                    best_score, best_rule = score, rule
        if best_rule and best_score >= self._threshold:
            self.on_command(best_rule["trigger"]["phrase"], best_rule)

    @staticmethod
    def list_microphones() -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({"index": idx, "name": dev.get("name", f"Micro {idx}")})
        except Exception:
            pass
        return devices
