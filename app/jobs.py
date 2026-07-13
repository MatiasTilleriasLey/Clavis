"""Cola de jobs de transcripción (RQ/Redis). Un solo worker corre la cola => como mucho un
job pesado a la vez, que es el límite de concurrencia que pide el threat model sin GPU (§6.27)."""
import os
import shutil
import uuid

from redis import Redis
from rq import Queue

from . import storage
from .extensions import db
from .pipeline import musicxml_to_pdf, separate_stems, transcribe  # patcheable en tests

QUEUE_NAME = "clavis"
JOB_TIMEOUT = 1800  # 30 min duro por job (defensa DoS, refuerza el tope de duración del paso 12)


def _redis():
    from flask import current_app
    return Redis.from_url(current_app.config["REDIS_URL"])


def enqueue_transcription(user_id, audio_path, work_dir, title, stems=None):
    """Crea el Job en DB y lo encola (o lo corre inline si RQ_ASYNC=False, para tests).
    `stems`: lista de instrumentos a separar; vacío => transcribe la mezcla completa."""
    from flask import current_app
    from .models import Job

    stems = stems or []
    job = Job(user_id=user_id, status="queued", work_dir=work_dir)
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.transcribe_job", job.id, audio_path, work_dir,
                           user_id, title, stems, job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        transcribe_job(job.id, audio_path, work_dir, user_id, title, stems)
    return job


def enqueue_ingest(user_id, url, work_dir, title, stems=None):
    """Job que descarga por URL (yt-dlp) y transcribe. La allowlist ya se validó en la ruta."""
    from flask import current_app
    from .models import Job

    stems = stems or []
    job = Job(user_id=user_id, status="queued", work_dir=work_dir)
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.ingest_job", job.id, url, work_dir, user_id, title, stems,
                           job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        ingest_job(job.id, url, work_dir, user_id, title, stems)
    return job


def ingest_job(job_id, url, work_dir, user_id, title, stems=None):
    from flask import current_app, has_app_context
    stems = stems or []

    def work(app):
        from .ingest import HARD_CAP_SECONDS, download_audio
        audio_path = download_audio(url, work_dir, HARD_CAP_SECONDS)
        return _transcribe_and_store(audio_path, work_dir, user_id, title, stems, app)

    if has_app_context():
        app = current_app._get_current_object()
        _execute(job_id, work_dir, app, lambda: work(app))
        return
    from app import create_app
    app = create_app()
    with app.app_context():
        _execute(job_id, work_dir, app, lambda: work(app))


def cancel(job, redis_conn):
    """Cancela el job en RQ (encolado o corriendo). Ownership se verifica en la ruta."""
    if job.rq_id:
        try:
            from rq.command import send_stop_command
            from rq.job import Job as RQJob
            rq_job = RQJob.fetch(job.rq_id, connection=redis_conn)
            if rq_job.get_status() == "started":
                send_stop_command(redis_conn, job.rq_id)  # mata el work-horse
            else:
                rq_job.cancel()  # lo saca de la cola
        except Exception:
            pass  # ya terminó o no existe; igual marcamos canceled abajo


def transcribe_job(job_id, audio_path, work_dir, user_id, title, stems=None):
    """Corre en el worker (sin app context) o inline en tests (con app context)."""
    from flask import current_app, has_app_context

    stems = stems or []
    if has_app_context():
        app = current_app._get_current_object()
        return _run(job_id, audio_path, work_dir, user_id, title, stems, app)
    from app import create_app
    app = create_app()
    with app.app_context():
        return _run(job_id, audio_path, work_dir, user_id, title, stems, app)


def _transcribe_one(wav, out_dir, instrument, title, user_id, app):
    """Transcribe un WAV (mezcla o stem) -> crea y devuelve un Score (sin commit)."""
    from .models import Score
    xml = transcribe(wav, out_dir)
    pdf = None
    mscore = app.config.get("MSCORE_BIN")
    if mscore:
        try:
            pdf = os.path.join(out_dir, "score.pdf")
            musicxml_to_pdf(xml, pdf, mscore)
        except Exception:
            app.logger.warning("export PDF falló", exc_info=True)
            pdf = None
    stored = uuid.uuid4().hex
    storage.save(user_id, stored, xml, pdf)
    display = title if instrument == "mezcla" else f"{title} — {instrument}"
    score = Score(user_id=user_id, title=display, instrument=instrument,
                  stored_uuid=stored, has_pdf=pdf is not None)
    db.session.add(score)
    return score


def _transcribe_and_store(audio_path, work_dir, user_id, title, stems, app):
    """Mezcla o stems -> uno o varios Score. Devuelve la lista (ya en la sesión, commiteada)."""
    if stems:
        stem_paths = separate_stems(audio_path, work_dir, stems)  # {instrumento: wav}
        if not stem_paths:
            raise RuntimeError("sin stems")
        units = list(stem_paths.items())
    else:
        units = [("mezcla", audio_path)]

    scores = []
    for instrument, wav in units:
        out_dir = os.path.join(work_dir, f"out_{instrument}")
        os.makedirs(out_dir, exist_ok=True)
        scores.append(_transcribe_one(wav, out_dir, instrument, title, user_id, app))
        if wav != audio_path:
            try:
                os.remove(wav)  # limpiar el stem WAV apenas se transcribió (§6.16)
            except OSError:
                pass
    db.session.commit()
    return scores


def _execute(job_id, work_dir, app, produce_scores):
    """Envuelve el ciclo de estado/errores/cleanup común a upload y URL.
    produce_scores() hace el trabajo pesado y devuelve la lista de Score."""
    from .models import Job
    job = db.session.get(Job, job_id)
    if job is None or job.status == "canceled":
        shutil.rmtree(work_dir, ignore_errors=True)
        return
    job.status = "started"
    db.session.commit()
    try:
        scores = produce_scores()
        job.status = "finished"
        job.score_id = scores[0].id
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.warning("job falló", exc_info=True)  # sin contenido (§logging)
        job = db.session.get(Job, job_id)
        if job is not None:
            job.status = "failed"
            job.error = "job_error"
            db.session.commit()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)  # audio/original nunca se persiste


def _run(job_id, audio_path, work_dir, user_id, title, stems, app):
    _execute(job_id, work_dir, app,
             lambda: _transcribe_and_store(audio_path, work_dir, user_id, title, stems, app))
