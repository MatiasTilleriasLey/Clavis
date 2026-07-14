"""Cola de jobs de transcripción (RQ/Redis). Un solo worker corre la cola => como mucho un
job pesado a la vez, que es el límite de concurrencia que pide el threat model sin GPU (§6.27)."""
import os
import shutil
import uuid

from redis import Redis
from rq import Queue

from . import storage
from .extensions import db
from .pipeline import separate_piano_hq, transcribe  # patcheable en tests

QUEUE_NAME = "clavis"
JOB_TIMEOUT = 1800  # 30 min duro por job (defensa DoS, refuerza el tope de duración del paso 12)


def _redis():
    from flask import current_app
    return Redis.from_url(current_app.config["REDIS_URL"])


def enqueue_transcription(user_id, audio_path, work_dir, title, separate=False):
    """Crea el Job en DB y lo encola (o lo corre inline si RQ_ASYNC=False, para tests).
    `separate`: si True, aísla el piano de la mezcla (Demucs) antes de transcribir."""
    from flask import current_app
    from .models import Job

    job = Job(user_id=user_id, status="queued", work_dir=work_dir)
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.transcribe_job", job.id, audio_path, work_dir,
                           user_id, title, separate, job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        transcribe_job(job.id, audio_path, work_dir, user_id, title, separate)
    return job


def enqueue_ingest(user_id, url, work_dir, title, separate=False):
    """Job que descarga por URL (yt-dlp) y transcribe. La allowlist ya se validó en la ruta."""
    from flask import current_app
    from .models import Job

    job = Job(user_id=user_id, status="queued", work_dir=work_dir)
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.ingest_job", job.id, url, work_dir, user_id, title, separate,
                           job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        ingest_job(job.id, url, work_dir, user_id, title, separate)
    return job


def enqueue_midi(user_id, midi_path, work_dir, title):
    """Job que genera la partitura desde un MIDI subido (sin transcripción, solo MuseScore)."""
    from flask import current_app
    from .models import Job

    job = Job(user_id=user_id, status="queued", work_dir=work_dir)
    db.session.add(job)
    db.session.commit()

    if current_app.config.get("RQ_ASYNC", True):
        q = Queue(QUEUE_NAME, connection=_redis())
        rq_job = q.enqueue("app.jobs.midi_job", job.id, midi_path, work_dir, user_id, title,
                           job_timeout=JOB_TIMEOUT)
        job.rq_id = rq_job.id
        db.session.commit()
    else:
        midi_job(job.id, midi_path, work_dir, user_id, title)
    return job


def midi_job(job_id, midi_path, work_dir, user_id, title):
    from flask import current_app, has_app_context

    def work(app):
        from .models import Score
        from .pipeline import midi_to_score
        _stage(job_id, "generando la partitura")
        out_dir = os.path.join(work_dir, "out")
        os.makedirs(out_dir, exist_ok=True)
        mscore = app.config.get("MSCORE_BIN")
        xml, pdf = midi_to_score(midi_path, out_dir, title=title, mscore_bin=mscore)
        midi = os.path.join(out_dir, "notes.mid")
        has_midi = os.path.exists(midi)
        has_pdf = bool(pdf) and os.path.exists(pdf)
        stored = uuid.uuid4().hex
        storage.save(user_id, stored, xml, pdf if has_pdf else None, midi if has_midi else None)
        score = Score(user_id=user_id, title=title, instrument="piano", stored_uuid=stored,
                      has_pdf=has_pdf, has_midi=has_midi)
        db.session.add(score)
        db.session.commit()
        return [score]

    if has_app_context():
        app = current_app._get_current_object()
        _execute(job_id, work_dir, app, lambda: work(app))
        return
    from app import create_app
    app = create_app()
    with app.app_context():
        _execute(job_id, work_dir, app, lambda: work(app))


def ingest_job(job_id, url, work_dir, user_id, title, separate=False):
    from flask import current_app, has_app_context

    def work(app):
        from .ingest import HARD_CAP_SECONDS, download_audio
        _stage(job_id, "descargando audio")
        audio_path = download_audio(url, work_dir, HARD_CAP_SECONDS)
        return _transcribe_and_store(job_id, audio_path, work_dir, user_id, title, separate, app)

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


STALE_AFTER = 3 * 3600  # backstop: un job no-terminal más viejo que esto se da por perdido


def reap_stale(user_id, redis_conn):
    """Reconcilia los jobs no-terminales de un usuario contra RQ. Si el worker murió sin
    actualizar la DB (SIGKILL, reboot, Redis reiniciado), el job queda 'queued'/'started'
    para siempre y el dashboard lo muestra como si siguiera en proceso. Acá lo marcamos
    'failed' cuando su job de RQ ya no existe / no está vivo, o cuando es demasiado viejo,
    y limpiamos su temp (el audio nunca se persiste, §4.5). Devuelve True si cambió algo."""
    from datetime import datetime, timezone

    from .models import Job as DBJob
    try:
        redis_conn.ping()
    except Exception:
        return False  # Redis caído: no podemos saber el estado real, no tocar nada
    stale = (DBJob.query.filter_by(user_id=user_id)
             .filter(DBJob.status.in_(("queued", "started"))).all())
    now, changed = datetime.now(timezone.utc), False
    for j in stale:
        alive = False
        if j.rq_id:
            try:
                from rq.job import Job as RQJob
                rq_job = RQJob.fetch(j.rq_id, connection=redis_conn)
                alive = rq_job.get_status(refresh=True) in ("queued", "started", "deferred", "scheduled")
            except Exception:
                alive = False  # el job de RQ ya no existe => el worker se perdió
        ca = j.created_at
        if ca is not None and ca.tzinfo is None:
            ca = ca.replace(tzinfo=timezone.utc)
        too_old = ca is not None and (now - ca).total_seconds() > STALE_AFTER
        if not alive or too_old:
            j.status = "failed"
            j.error = j.error or "worker_perdido"
            if j.work_dir:
                shutil.rmtree(j.work_dir, ignore_errors=True)
            changed = True
    if changed:
        db.session.commit()
    return changed


def transcribe_job(job_id, audio_path, work_dir, user_id, title, separate=False):
    """Corre en el worker (sin app context) o inline en tests (con app context)."""
    from flask import current_app, has_app_context

    if has_app_context():
        app = current_app._get_current_object()
        return _run(job_id, audio_path, work_dir, user_id, title, separate, app)
    from app import create_app
    app = create_app()
    with app.app_context():
        return _run(job_id, audio_path, work_dir, user_id, title, separate, app)


def _transcribe_one(wav, out_dir, title, user_id, app):
    """Transcribe un WAV de piano -> crea y devuelve un Score (sin commit)."""
    from .models import Score
    mscore = app.config.get("MSCORE_BIN")
    xml, pdf = transcribe(wav, out_dir, title=title, mscore_bin=mscore)
    midi = os.path.join(out_dir, "notes.mid")
    has_midi = os.path.exists(midi)
    has_pdf = bool(pdf) and os.path.exists(pdf)
    stored = uuid.uuid4().hex
    storage.save(user_id, stored, xml, pdf if has_pdf else None, midi if has_midi else None)
    score = Score(user_id=user_id, title=title, instrument="piano", stored_uuid=stored,
                  has_pdf=has_pdf, has_midi=has_midi)
    db.session.add(score)
    return score


def _stage(job_id, text):
    """Actualiza la fase visible del job (progreso para el frontend)."""
    from .models import Job
    job = db.session.get(Job, job_id)
    if job is not None:
        job.stage = text
        db.session.commit()


def _transcribe_and_store(job_id, audio_path, work_dir, user_id, title, separate, app):
    """Transcribe el piano -> un Score. Si `separate`, aísla primero el piano de la mezcla."""
    wav = audio_path
    if separate:
        _stage(job_id, "aislando el piano (roformer + demucs)")
        # cascada de alta calidad: roformer quita la voz + demucs saca el piano (lento pero mejor)
        wav = separate_piano_hq(audio_path, work_dir)

    _stage(job_id, "transcribiendo piano")
    out_dir = os.path.join(work_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    score = _transcribe_one(wav, out_dir, title, user_id, app)
    if wav != audio_path:
        try:
            os.remove(wav)  # limpiar el stem WAV apenas se transcribió (§6.16)
        except OSError:
            pass
    db.session.commit()
    return [score]


def _notify_ready(user_id, score_id, app):
    """Avisa por mail que la partitura está lista (no-op si no hay SMTP configurado)."""
    from . import mailer
    from .models import User
    try:
        user = db.session.get(User, user_id)
        if user is None:
            return
        link = f"{app.config.get('BASE_URL', '')}/score/{score_id}"
        mailer.send(user.email, "Tu partitura de Clavis está lista",
                    f"Hola {user.name or ''}\n\nTu transcripción terminó. Vela acá:\n{link}\n")
    except Exception:
        app.logger.warning("notificación de job falló", exc_info=True)


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
        _notify_ready(job.user_id, scores[0].id, app)
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


def _run(job_id, audio_path, work_dir, user_id, title, separate, app):
    _execute(job_id, work_dir, app,
             lambda: _transcribe_and_store(job_id, audio_path, work_dir, user_id, title, separate, app))
