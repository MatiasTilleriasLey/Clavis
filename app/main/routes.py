import os
import tempfile
import uuid

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, send_file, url_for)
from flask_login import current_user
from werkzeug.utils import secure_filename

from .. import storage
from ..audio import detect_audio_kind
from ..auth.routes import admin_required, verified_required
from ..extensions import db
from ..jobs import cancel as cancel_job
from ..jobs import enqueue_ingest, enqueue_transcription
from ..models import Job, Score, User
from .forms import UploadForm

bp = Blueprint("main", __name__)


def _owned_score(score_id):
    """Trae la partitura SOLO si es del usuario de la sesión (defensa IDOR, §4.8).
    Filtra por id Y user_id en la misma query — nunca busca por id y chequea después."""
    return Score.query.filter_by(id=score_id, user_id=current_user.id).first_or_404()


def _owned_job(job_id):
    """Mismo criterio para jobs: ownership en cancelación/estado (§6.28)."""
    return Job.query.filter_by(id=job_id, user_id=current_user.id).first_or_404()


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

    # Instrumentos elegidos, filtrados contra la allowlist STEM_MAP (nada externo al subprocess).
    from ..pipeline import STEM_MAP
    stems = [s for s in request.form.getlist("stems") if s in STEM_MAP]

    title = (os.path.splitext(f.filename or "audio")[0] or "audio")[:200]  # solo display
    # work_dir persiste hasta que el worker termine y lo limpie (audio nunca se persiste).
    work_dir = tempfile.mkdtemp(prefix="clavis_")
    src = os.path.join(work_dir, f"{uuid.uuid4().hex}.{kind}")
    f.save(src)
    job = enqueue_transcription(current_user.id, src, work_dir, title, stems)
    return redirect(url_for("main.job_view", job_id=job.id))


@bp.post("/ingest")
@verified_required
def ingest():
    from ..ingest import (HARD_CAP_SECONDS, SOFT_CAP_SECONDS, is_allowed_url,
                          probe)
    from ..pipeline import STEM_MAP

    url = (request.form.get("url") or "").strip()
    # Allowlist validada ACÁ, antes de tocar yt-dlp (§6.4). Corta SSRF/dominios arbitrarios.
    if not is_allowed_url(url):
        flash("Link no permitido. Solo YouTube, Instagram o TikTok.")
        return redirect(url_for("auth.dashboard"))

    stems = [s for s in request.form.getlist("stems") if s in STEM_MAP]
    confirmed = request.form.get("confirm") == "1"
    try:
        duration, title = probe(url)
    except Exception:
        current_app.logger.warning("probe de URL falló", exc_info=True)  # sin contenido (§logging)
        flash("No se pudo leer el link.")
        return redirect(url_for("auth.dashboard"))

    # Tope duro server-side, no evadible por el usuario (§4.85, §6.26).
    if duration is not None and duration > HARD_CAP_SECONDS:
        flash("El contenido supera el tope de 60 minutos.")
        return redirect(url_for("auth.dashboard"))
    # Advertencia blanda (UX): pedir confirmación explícita si supera 15 min.
    if duration is not None and duration > SOFT_CAP_SECONDS and not confirmed:
        return render_template("confirm_long.html", url=url, stems=stems, minutes=duration // 60)

    work_dir = tempfile.mkdtemp(prefix="clavis_")
    job = enqueue_ingest(current_user.id, url, work_dir, title, stems)
    return redirect(url_for("main.job_view", job_id=job.id))


@bp.get("/job/<int:job_id>")
@verified_required
def job_view(job_id):
    job = _owned_job(job_id)
    return render_template("job.html", job=job)


@bp.get("/job/<int:job_id>/status")
@verified_required
def job_status(job_id):
    job = _owned_job(job_id)
    return {"status": job.status, "score_id": job.score_id}


@bp.post("/job/<int:job_id>/cancel")
@verified_required
def job_cancel(job_id):
    job = _owned_job(job_id)
    if job.status in ("queued", "started"):
        from ..jobs import _redis
        cancel_job(job, _redis())
        # el SIGKILL al workhorse saltea el finally del job => limpiamos su temp acá
        # para no dejar el audio en disco (§4.5, "el audio nunca se persiste").
        if job.work_dir:
            import shutil
            shutil.rmtree(job.work_dir, ignore_errors=True)
        job.status = "canceled"
        db.session.commit()
    flash("Job cancelado.")
    return redirect(url_for("auth.dashboard"))


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


@bp.get("/admin")
@admin_required
def admin():
    from sqlalchemy import func
    users = User.query.order_by(User.created_at.desc()).all()
    job_counts = dict(db.session.query(Job.status, func.count()).group_by(Job.status).all())
    return render_template("admin.html", users=users, job_counts=job_counts)


@bp.post("/score/<int:score_id>/delete")
@verified_required
def score_delete(score_id):
    score = _owned_score(score_id)
    storage.delete(current_user.id, score.stored_uuid)
    db.session.delete(score)
    db.session.commit()
    flash("Partitura borrada.")
    return redirect(url_for("auth.dashboard"))
