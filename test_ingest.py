"""Checks de la ingesta por URL: allowlist de dominios (defensa SSRF, §4.2/§6.4) y descarga de
video para la transcripción asistida (§6.6/§6.26). Corre: .venv/bin/python test_ingest.py"""
import os
import re
import shutil
import tempfile

import app.ingest as ing
from app.ingest import is_allowed_url


def test_download_video():
    """La descarga de video hereda los controles de la de audio: nombre interno UUID (nunca
    derivado del título del video), tope de duración server-side y timeout."""
    work = tempfile.mkdtemp(prefix="clavis_test_")
    real, seen = ing._ytdlp, {}

    def fake(args, timeout):
        seen.update(args=args, timeout=timeout)
        tmpl = args[args.index("-o") + 1]
        open(tmpl.replace(".%(ext)s", ".mp4"), "wb").write(b"video")
    try:
        ing._ytdlp = fake
        path = ing.download_video("https://youtu.be/x", work, max_seconds=600)
        assert os.path.exists(path), "no encontró el archivo descargado"
        assert re.fullmatch(r"[0-9a-f]{32}\.mp4", os.path.basename(path)), \
            f"el nombre debe ser un UUID interno, es {os.path.basename(path)}"
        assert "duration < 600" in seen["args"], "no pasó el tope de duración a yt-dlp"
        assert seen["timeout"] == ing.DOWNLOAD_TIMEOUT, "descarga sin timeout"
        sort = seen["args"][seen["args"].index("-S") + 1]
        assert f"res:{ing.VIDEO_MAX_HEIGHT}" in sort, "no limitó la resolución"
        # sin esto YouTube manda AV1, que el ffmpeg de OpenCV no decodifica: se baja el video
        # entero, se leen 0 frames y la transcripción cae en silencio al audio solo.
        assert sort.startswith("vcodec:h264"), "H.264 debe pesar más que la resolución"

        # sin archivo descargado (rechazado por duración) falla ruidoso, no devuelve basura
        ing._ytdlp = lambda args, timeout: None
        try:
            ing.download_video("https://youtu.be/x", tempfile.mkdtemp(prefix="clavis_test_"))
            raise AssertionError("una descarga vacía debería fallar")
        except RuntimeError:
            pass
        print("OK: descarga de video (UUID, tope de duración, timeout, 720p, descarga vacía)")
    finally:
        ing._ytdlp = real
        shutil.rmtree(work, ignore_errors=True)


def run():
    ok = [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "http://youtube.com/x",
        "https://www.instagram.com/reel/x",
        "https://vm.tiktok.com/x",
        "https://rr3---sn-x.googlevideo.com/videoplayback?x=1",
    ]
    bad = [
        "http://localhost/x",                    # servicio local
        "http://127.0.0.1:6379/",                # pivoteo a Redis
        "http://169.254.169.254/latest/meta",    # metadata de cloud
        "http://evil.com/x",                     # dominio arbitrario
        "https://youtube.com.evil.com/x",        # sufijo engañoso
        "https://evilyoutube.com/x",             # sin punto separador
        "file:///etc/passwd",                    # esquema no http
        "javascript:alert(1)",                   # esquema peligroso
        "",                                      # vacío
        "not a url",
    ]
    for u in ok:
        assert is_allowed_url(u), f"debería permitir: {u}"
    for u in bad:
        assert not is_allowed_url(u), f"NO debería permitir: {u}"
    print(f"OK: allowlist verificada ({len(ok)} permitidos, {len(bad)} rechazados)")
    test_download_video()


if __name__ == "__main__":
    run()
