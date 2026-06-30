"""Mise a jour auto via les releases GitHub (telecharge, extrait, remplace).

Le swap de l'exe en cours d'execution se fait par un script .bat detache :
il attend la fermeture de l'app, copie les nouveaux fichiers, puis relance.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests

from . import version
from .config import DATA_DIR

ProgressCb = Callable[[float, str], None]

API_LATEST = (
    f"https://api.github.com/repos/{version.GITHUB_OWNER}/"
    f"{version.GITHUB_REPO}/releases/latest"
)
UPDATE_DIR = DATA_DIR / "updates"


def _parse(v: str) -> tuple[int, ...]:
    nums = "".join(c if (c.isdigit() or c == ".") else " " for c in v).split()
    if not nums:
        return (0,)
    return tuple(int(x) for x in nums[0].split(".") if x.isdigit())


def _is_newer(latest: str, current: str) -> bool:
    a, b = _parse(latest), _parse(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def check() -> dict[str, Any]:
    """Interroge GitHub. Retourne l'etat de mise a jour."""
    cur = version.__version__
    try:
        resp = requests.get(API_LATEST, timeout=15,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 404:
            return {"ok": True, "available": False, "current": cur,
                    "message": "Aucune release publiee pour le moment."}
        resp.raise_for_status()
        data = resp.json()
        tag = str(data.get("tag_name", "")).strip()
        assets = data.get("assets", [])
        dl = ""
        for a in assets:
            name = str(a.get("name", "")).lower()
            if name.endswith(".zip"):
                dl = a.get("browser_download_url", "")
                break
        if not dl:
            dl = data.get("zipball_url", "")
        available = bool(tag) and _is_newer(tag, cur) and bool(dl)
        return {
            "ok": True, "available": available, "current": cur,
            "latest": tag, "download_url": dl,
            "notes": data.get("body", "") or "",
            "message": ("Mise a jour disponible." if available
                        else "Vous avez la derniere version."),
        }
    except Exception as exc:
        return {"ok": False, "available": False, "current": cur,
                "message": f"Verification impossible: {exc}"}


def _download(url: str, dest: Path, cb: ProgressCb) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                cb((done / total * 100) if total else 0.0, "telechargement")


def apply(download_url: str, cb: ProgressCb) -> dict[str, Any]:
    """Telecharge + extrait + lance le script de remplacement. L'app doit ensuite quitter."""
    if not getattr(sys, "frozen", False):
        return {"ok": False, "message": "Mise a jour auto disponible seulement sur l'exe."}
    if not download_url:
        return {"ok": False, "message": "Aucun fichier de mise a jour."}
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = UPDATE_DIR / "update.zip"
        cb(0.0, "telechargement")
        _download(download_url, zip_path, cb)
        cb(99.0, "extraction")
        extract_dir = UPDATE_DIR / "new"
        if extract_dir.exists():
            import shutil
            shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        # la racine de l'app = dossier contenant CameraRecognizer.exe
        src_root = _find_app_root(extract_dir)
        if src_root is None:
            return {"ok": False, "message": "Archive invalide (exe introuvable)."}
        install_dir = Path(sys.executable).resolve().parent
        bat = _write_swap_script(src_root, install_dir)
        subprocess.Popen(["cmd", "/c", str(bat)],
                        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        cb(100.0, "redemarrage")
        return {"ok": True, "message": "Mise a jour prete. L'app va redemarrer."}
    except Exception as exc:
        return {"ok": False, "message": f"Echec de la mise a jour: {exc}"}


def _find_app_root(folder: Path) -> Path | None:
    exe = "CameraRecognizer.exe"
    if (folder / exe).exists():
        return folder
    for p in folder.rglob(exe):
        return p.parent
    return None


def _write_swap_script(src: Path, dst: Path) -> Path:
    exe = dst / "CameraRecognizer.exe"
    bat = UPDATE_DIR / "swap.bat"
    # attend la fermeture, copie (sauf data\), relance, se supprime
    content = (
        "@echo off\r\n"
        "ping 127.0.0.1 -n 3 >nul\r\n"
        f'robocopy "{src}" "{dst}" /MIR /XD "{dst}\\data" /R:3 /W:1 >nul\r\n'
        f'start "" "{exe}"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n'
    )
    bat.write_text(content, encoding="utf-8")
    return bat
