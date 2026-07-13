import os
import shutil
import tempfile
import uuid

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, send_file, url_for)
from flask_login import current_user
from werkzeug.utils import secure_filename

from .. import storage
from ..audio import detect_audio_kind
from ..auth.routes import verified_required
from ..extensions import db
from ..models import Score
from ..pipeline import musicxml_to_pdf, transcribe
from .forms import UploadForm

bp = Blueprint("main", __name__)


def _owned_score(score_id):
    """Trae la partitura SOLO si es del usuario de la sesión (defensa IDOR, §4.8).
    Filtra por id Y user_id en la misma query — nunca busca por id y chequea después."""
    return Score.query.filter_by(id=score_id, user_id=current_user.id).first_or_404()


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

    title = (os.path.splitext(f.filename or "audio")[0] or "audio")[:200]  # solo display
    tmpdir = tempfile.mkdtemp(prefix="clavis_")
    try:
        src = os.path.join(tmpdir, f"{uuid.uuid4().hex}.{kind}")
        f.save(src)
        # ponytail: sync por ahora (paso 9). Pasa a la cola en el paso 10.
        xml_path = transcribe(src, tmpdir)

        pdf_path = None
        mscore = current_app.config.get("MSCORE_BIN")
        if mscore:
            try:
                pdf_path = os.path.join(tmpdir, "score.pdf")
                musicxml_to_pdf(xml_path, pdf_path, mscore)
            except Exception:
                current_app.logger.warning("export PDF falló", exc_info=True)
                pdf_path = None

        stored = uuid.uuid4().hex
        storage.save(current_user.id, stored, xml_path, pdf_path)
        score = Score(user_id=current_user.id, title=title, instrument="mezcla",
                      stored_uuid=stored, has_pdf=pdf_path is not None)
        db.session.add(score)
        db.session.commit()
    except Exception:
        current_app.logger.warning("fallo de transcripción", exc_info=True)
        flash("No se pudo transcribir el audio. Probá con otro archivo.")
        return redirect(url_for("auth.dashboard"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # audio original nunca se persiste

    return redirect(url_for("main.score_view", score_id=score.id))


@bp.get("/score/<int:score_id>")
@verified_required
def score_view(score_id):
    score = _owned_score(score_id)
    return render_template("score.html", score=score)


@bp.get("/score/<int:score_id>/musicxml")
@verified_required
def score_musicxml(score_id):
    score = _owned_score(score_id)
    path = storage.path_for(current_user.id, score.stored_uuid, "musicxml")
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="application/xml")


@bp.get("/score/<int:score_id>/pdf")
@verified_required
def score_pdf(score_id):
    score = _owned_score(score_id)
    path = storage.path_for(current_user.id, score.stored_uuid, "pdf")
    if not score.has_pdf or not os.path.exists(path):
        abort(404)
    name = (secure_filename(score.title) or "partitura") + ".pdf"
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name=name)


@bp.post("/score/<int:score_id>/delete")
@verified_required
def score_delete(score_id):
    score = _owned_score(score_id)
    storage.delete(current_user.id, score.stored_uuid)
    db.session.delete(score)
    db.session.commit()
    flash("Partitura borrada.")
    return redirect(url_for("auth.dashboard"))
