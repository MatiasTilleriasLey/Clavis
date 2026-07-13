"""Ingesta de audio por URL (yt-dlp). Toda la superficie sensible del threat model §4.2:
allowlist de dominios validada ANTES de invocar yt-dlp (§6.4), shell=False (§6.5),
nombres UUID (§6.6), tope duro de duración (§6.26) y timeouts (§6.7)."""
import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

# Allowlist estricta: solo estos dominios (y subdominios) llegan a yt-dlp.
ALLOWED_HOSTS = (
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com",
    "googlevideo.com", "cdninstagram.com", "tiktokcdn.com",
)

PROBE_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 900
SOFT_CAP_SECONDS = 15 * 60   # advertencia de UX (§4.85)
HARD_CAP_SECONDS = 60 * 60   # tope duro server-side, no evadible (§6.26)


def is_allowed_url(url):
    """True solo si el host está en la allowlist. Se valida acá, no se delega a yt-dlp (§6.4)."""
    try:
        p = urlparse(url or "")
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_HOSTS)


def _ytdlp(args, timeout):
    # YouTube ahora exige un runtime de JS para la extracción; usamos node (ya instalado).
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--no-playlist", "--js-runtimes", "node", *args],
        check=True, timeout=timeout, capture_output=True, text=True, shell=False)


def probe(url):
    """Metadata sin descargar. Devuelve (duracion_segundos|None, titulo)."""
    out = _ytdlp(["--skip-download", "--print", "%(duration)s\n%(title)s", url], PROBE_TIMEOUT)
    lines = out.stdout.strip().splitlines()
    duration = None
    if lines:
        try:
            duration = int(float(lines[0]))
        except ValueError:
            duration = None
    title = (lines[1] if len(lines) > 1 else "audio")[:200]  # solo display (Jinja escapa)
    return duration, title


def download_audio(url, work_dir, max_seconds=HARD_CAP_SECONDS):
    """Descarga solo el audio. Nombre interno UUID (§6.6); match-filter corta por duración
    server-side (§6.26) aunque el probe se haya evadido. Devuelve el path del audio."""
    tmpl = os.path.join(work_dir, uuid.uuid4().hex + ".%(ext)s")
    _ytdlp(["--match-filter", f"duration < {int(max_seconds)}",
            "-x", "--audio-format", "mp3", "-o", tmpl, url], DOWNLOAD_TIMEOUT)
    mp3s = [f for f in os.listdir(work_dir) if f.endswith(".mp3")]
    if not mp3s:
        raise RuntimeError("descarga vacía o rechazada por duración")
    return os.path.join(work_dir, mp3s[0])
