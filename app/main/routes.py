import os
import shutil
import tempfile
import uuid

from flask import Blueprint, flash, redirect, url_for

from ..audio import detect_audio_kind
from ..auth.routes import verified_required
from .forms import UploadForm

bp = Blueprint("main", __name__)


@bp.post("/upload")
@verified_required
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e)
        return redirect(url_for("auth.dashboard"))

    f = form.audio.data
    head = f.stream.read(12)
    f.stream.seek(0)
    kind = detect_audio_kind(head)
    if kind is None:
        flash("El archivo no parece un audio válido (MP3/WAV/M4A/MP4).")
        return redirect(url_for("auth.dashboard"))

    # tempfile.mkdtemp => 0700 y nombre no predecible (§4.5, §6.11). Nombre interno UUID (§6.6).
    tmpdir = tempfile.mkdtemp(prefix="clavis_")
    try:
        path = os.path.join(tmpdir, f"{uuid.uuid4().hex}.{kind}")
        f.save(path)
        size_mb = os.path.getsize(path) / 1024 / 1024
        # ponytail: sin pipeline todavía (paso 6). Validamos y descartamos: el audio no se persiste.
        flash(f"Archivo validado: {kind.upper()}, {size_mb:.1f} MB. "
              f"(Descartado — la transcripción llega en el próximo paso.)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # cleanup garantizado, incluso si falla
    return redirect(url_for("auth.dashboard"))
