"""Cola de jobs de transcripción (RQ/Redis). Un solo worker corre la cola => como mucho un
job pesado a la vez, que es el límite de concurrencia que pide el threat model sin GPU (§6.27)."""
import os
import shutil
import uuid

from redis import Redis
from rq import Queue

from . import storage
from .extensions import db
from .pipeline import musicxml_to_pdf, transcribe  # patcheable en tests

QUEUE_NAME = "clavis"
JOB_TIMEOUT = 1800  # 30 min duro por job (defensa DoS, refuerza el tope de duración del paso 12)


def _redis():
    from flask import current_app
    return Redis.from_url(current_app.config["REDIS_URL"])


def enqueue_transcription(user_id, audio_path, work_dir, title):
    """Crea el Job en DB y lo encola (o lo corre inline si RQ_ASYNC=False, para tests)."""
    from flask import current_app
    from .models import Job

    job = Job(user_id=user_id, status="queued")
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.transcribe_job", job.id, audio_path, work_dir,
                           user_id, title, job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        transcribe_job(job.id, audio_path, work_dir, user_id, title)
    return job


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


def transcribe_job(job_id, audio_path, work_dir, user_id, title):
    """Corre en el worker (sin app context) o inline en tests (con app context)."""
    from flask import current_app, has_app_context

    if has_app_context():
        return _run(job_id, audio_path, work_dir, user_id, title, current_app._get_current_object())
    from app import create_app
    app = create_app()
    with app.app_context():
        return _run(job_id, audio_path, work_dir, user_id, title, app)


def _run(job_id, audio_path, work_dir, user_id, title, app):
    from .models import Job, Score
    job = db.session.get(Job, job_id)
    if job is None or job.status == "canceled":
        shutil.rmtree(work_dir, ignore_errors=True)
        return
    job.status = "started"
    db.session.commit()
    try:
        xml_path = transcribe(audio_path, work_dir)
        pdf_path = None
        mscore = app.config.get("MSCORE_BIN")
        if mscore:
            try:
                pdf_path = os.path.join(work_dir, "score.pdf")
                musicxml_to_pdf(xml_path, pdf_path, mscore)
            except Exception:
                app.logger.warning("export PDF falló", exc_info=True)
                pdf_path = None
        stored = uuid.uuid4().hex
        storage.save(user_id, stored, xml_path, pdf_path)
        score = Score(user_id=user_id, title=title, instrument="mezcla",
                      stored_uuid=stored, has_pdf=pdf_path is not None)
        db.session.add(score)
        db.session.commit()
        job.status = "finished"
        job.score_id = score.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.warning("job de transcripción falló", exc_info=True)  # sin contenido (§logging)
        job = db.session.get(Job, job_id)
        if job is not None:
            job.status = "failed"
            job.error = "transcription_error"
            db.session.commit()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)  # audio original nunca se persiste
