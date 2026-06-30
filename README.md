# Camera Recognizer

Assistant local pour Windows 10/11 qui **voit** (caméra), **entend** (micro), **parle** (TTS) et **exécute des actions** personnalisables. 100 % hors-ligne après le téléchargement initial des modèles. Aucune donnée ne quitte la machine.

- **Caméra** : flux annoté temps réel (boîtes de visages, noms, score, indicateur de mouvement).
- **Reconnaissance faciale** : enrôlement par upload ou capture caméra, embeddings SFace, seuil réglable.
- **Salutation auto** quand une personne connue apparaît (anti-répétition réglable).
- **Commandes vocales** : Vosk en streaming + matching fuzzy (rapidfuzz).
- **TTS** Piper, voix FR changeable.
- **Détection de mouvement** (MOG2) comme déclencheur.
- **Moteur d'actions** déclencheur → action(s) chaînables : `say`, `open_url`, `launch_app`, `alarm`, `keys`, `notification`, `shell`, `delay`.

## Vie privée

Tout reste **local**, dans le dossier portable `./data/` (à côté de l'exe). Visages, photos et embeddings sont stockés en clair sur votre machine, **jamais envoyés en ligne**. Seule connexion réseau : le téléchargement des modèles au premier lancement.

Si `./data/` n'est pas inscriptible (ex. dossier protégé), l'app bascule sur `%LOCALAPPDATA%\CameraRecognizer\`.

## Lancement depuis les sources

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Au 1er démarrage, va dans **Paramètres → Modèles locaux → Télécharger les modèles manquants** (barre de progression). L'app démarre même hors-ligne ; chaque module s'active dès que son modèle est présent.

> Prérequis : le runtime **WebView2** (préinstallé sur Windows 10/11 à jour). Sinon, installez « Microsoft Edge WebView2 Runtime » (Evergreen, sans admin en mode utilisateur).

## Build de l'exe portable (sans admin)

```powershell
python build.py            # --onedir (recommandé) -> dist/CameraRecognizer/CameraRecognizer.exe
python build.py --onefile  # mono-fichier (démarrage plus lent)
```

Le dossier `dist/CameraRecognizer/` est **portable** : copiez-le où vous voulez et lancez `CameraRecognizer.exe` par double-clic. Aucune installation, aucun droit administrateur, aucune écriture hors `./data/`.

## Modèles

Téléchargés automatiquement dans `data/models/` :

| Module | Modèle | Emplacement attendu |
|--------|--------|---------------------|
| Détection visage | YuNet (OpenCV Zoo) | `data/models/face/face_detection_yunet_2023mar.onnx` |
| Reconnaissance | SFace (OpenCV Zoo) | `data/models/face/face_recognition_sface_2021dec.onnx` |
| STT | Vosk FR small 0.22 | `data/models/stt/vosk-model-small-fr-0.22/` |
| TTS (voix) | Piper `fr_FR-siwis-medium` | `data/models/tts/fr_FR-siwis-medium.onnx` (+ `.onnx.json`) |
| TTS (moteur) | Binaire Piper Windows | `data/models/tts/piper/piper.exe` |

**Dépôt manuel (hors-ligne)** : créez l'arborescence ci-dessus et déposez-y les fichiers depuis OpenCV Zoo (GitHub `opencv/opencv_zoo`), `alphacephei.com/vosk/models`, et Hugging Face `rhasspy/piper-voices`. Relancez l'app.

**Ajouter une voix TTS** : déposez un couple `*.onnx` + `*.onnx.json` Piper dans `data/models/tts/`, puis sélectionnez-la dans **Paramètres → Voix TTS**.

**Build 100 % offline** : copiez un dossier `data/models/` pré-rempli à côté de l'exe avant distribution.

## Dépannage

- **Caméra occupée / écran « Caméra arrêtée »** : fermez les autres apps utilisant la webcam, vérifiez l'index caméra dans Paramètres.
- **Pas de son TTS** : vérifiez qu'une voix Piper est présente et sélectionnée.
- **WebView2 manquant** : installez le runtime Evergreen (mode utilisateur, sans admin).
- **Touches `keys` sans effet** : la fenêtre cible doit avoir le focus ; `pyautogui` fonctionne sans admin mais pas sur fenêtres élevées (admin).

## Architecture

```
main.py            point d'entrée (uvicorn + fenêtre pywebview)
app/server.py      routes FastAPI, /video_feed (MJPEG), /ws (events)
app/camera.py      capture OpenCV, détection visage + mouvement, annotation HUD
app/face.py        enrôlement, embeddings SFace, reconnaissance
app/voice.py       STT Vosk streaming + matching commandes (rapidfuzz)
app/tts.py         synthèse Piper, file d'attente, voix sélectionnable
app/actions.py     moteur d'actions + alarme en boucle
app/triggers.py    routeur déclencheur → actions
app/config.py      config JSON portable (écriture atomique)
app/models.py      téléchargement + vérification des modèles
web/               UI (HTML/CSS/JS vanilla)
```
```
