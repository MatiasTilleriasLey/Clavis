import base64
import os
import shutil
import tempfile
import uuid

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   url_for)

from ..audio import detect_audio_kind
from ..auth.routes import verified_required
from ..pipeline import transcribe
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
        src = os.path.join(tmpdir, f"{uuid.uuid4().hex}.{kind}")
        f.save(src)
        # ponytail: sync por ahora (paso 6-7, para validar el render). Pasa a la cola en el paso 10.
        xml_path = transcribe(src, tmpdir)
        xml_b64 = base64.b64encode(open(xml_path, "rb").read()).decode()
    except Exception:
        # sin filtrar detalles al usuario; log técnico sin contenido (nombres son UUID) (§logging)
        current_app.logger.warning("fallo de transcripción", exc_info=True)
        flash("No se pudo transcribir el audio. Probá con otro archivo.")
        return redirect(url_for("auth.dashboard"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # cleanup garantizado (audio no se persiste)

    return render_template("result.html", xml_b64=xml_b64, kind=kind.upper())
