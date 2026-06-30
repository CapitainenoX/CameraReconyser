"""Construit l'exe portable Windows avec PyInstaller (--onedir, sans admin).

Usage :
    python build.py            # build --onedir (recommande)
    python build.py --onefile  # variant mono-fichier (demarrage plus lent)

Resultat : dist/CameraRecognizer/CameraRecognizer.exe
- Aucun droit admin requis a l'execution.
- Les modeles ne sont PAS embarques : telecharges au 1er lancement dans ./data/.
  Pour un build 100% offline, copie data/models/ pre-rempli a cote de l'exe.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";"  # separateur add-data Windows


def main() -> None:
    onefile = "--onefile" in sys.argv
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "CameraRecognizer",
        "--windowed",                       # pas de console
        "--onefile" if onefile else "--onedir",
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--add-data", f"web{SEP}web",       # UI embarquee
        "--add-data", f"assets{SEP}assets",  # icone runtime
        # collecte des binaires/donnees natifs souvent oublies
        "--collect-all", "vosk",
        "--collect-all", "mediapipe",
        "--collect-data", "cv2",
        "--hidden-import", "sounddevice",
        "--hidden-import", "webview.platforms.edgechromium",
        "main.py",
    ]
    print("PyInstaller:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    target = "dist/CameraRecognizer.exe" if onefile else "dist/CameraRecognizer/CameraRecognizer.exe"
    print(f"\nTermine. Executable : {target}")
    print("Lance-le par double-clic (aucun admin requis). Les modeles se telechargent au 1er demarrage.")


if __name__ == "__main__":
    main()
