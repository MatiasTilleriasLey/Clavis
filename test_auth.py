"""Checks de seguridad del flujo nuevo: registro sin verificación, primer-admin, 2FA, perfil,
upload, IDOR, jobs, allowlist, admin. Corre: .venv/bin/python test_auth.py"""
import os
import tempfile
from io import BytesIO

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pyotp  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import EmailToken, Job, Score, User  # noqa: E402


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SESSION_COOKIE_SECURE = False
    STORAGE_ROOT = tempfile.mkdtemp()
    MSCORE_BIN = None
    RQ_ASYNC = False


def run():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()

    def register(c, email, name, pw):
        return c.post("/register", data={"email": email, "name": name, "password": pw},
                      follow_redirects=True)

    def login(c, email, pw):
        return c.post("/login", data={"email": email, "password": pw}, follow_redirects=True)

    # 1. Primer registro => admin, y entra SIN verificación de email.
    register(app.test_client(), "a@x.com", "Ana", "clave1234")
    with app.app_context():
        a = db.session.scalar(db.select(User).filter_by(email="a@x.com"))
        assert a is not None and a.is_admin, "el primer usuario no quedó admin"
        a_id = a.id
    admin = app.test_client()
    assert b"Dashboard" in login(admin, "a@x.com", "clave1234").data, "no entró sin verificación"

    # 2. Segundo usuario NO es admin.
    register(app.test_client(), "b@x.com", "Beto", "clave1234")
    with app.app_context():
        assert not db.session.scalar(db.select(User).filter_by(email="b@x.com")).is_admin

    # 3. Email duplicado => rechazado, no duplica.
    r = register(app.test_client(), "a@x.com", "Otra", "clave1234")
    assert b"ya est\xc3\xa1 registrado" in r.data, "no rechazó email duplicado"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 2

    # 4. Login con password incorrecta => mensaje genérico, sin dashboard (anti-enum).
    r = login(app.test_client(), "a@x.com", "malamala")
    assert b"incorrectos" in r.data and b"Dashboard" not in r.data

    # 5. Perfil: cambio de contraseña exige la actual; la vieja deja de servir.
    admin.post("/profile/password",
               data={"current_password": "clave1234", "new_password": "nueva9999"},
               follow_redirects=True)
    assert b"Dashboard" not in login(app.test_client(), "a@x.com", "clave1234").data, "password vieja sirvió"
    assert b"Dashboard" in login(app.test_client(), "a@x.com", "nueva9999").data, "password nueva no sirvió"

    # 6. Perfil: cambio de email exige la contraseña.
    admin.post("/profile/email", data={"email": "ana@x.com", "current_password": "nueva9999"},
               follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, a_id).email == "ana@x.com", "no cambió el email"

    # 7. 2FA: activar y que el login lo exija.
    admin.post("/profile/2fa/setup")
    with admin.session_transaction() as s:
        secret = s["pending_totp"]
    admin.post("/profile/2fa/enable", data={"code": pyotp.TOTP(secret).now()}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, a_id).has_2fa, "no activó 2FA"
    # login: password correcta pero pide 2FA, no entra todavía
    c2fa = app.test_client()
    r = c2fa.post("/login", data={"email": "ana@x.com", "password": "nueva9999"}, follow_redirects=True)
    assert b"dos pasos" in r.data and b"Dashboard" not in r.data, "no pidió el segundo factor"
    # código incorrecto => no entra
    assert b"Dashboard" not in c2fa.post("/login/2fa", data={"code": "000000"}, follow_redirects=True).data
    # código correcto => entra
    assert b"Dashboard" in c2fa.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()},
                                     follow_redirects=True).data, "no entró con el 2FA correcto"

    # 8. Reset de contraseña por token (emitido directo; el email es no-op sin SMTP).
    with app.app_context():
        tok = EmailToken.issue(db.session.get(User, a_id), "reset", 60)
        db.session.commit()
    r = app.test_client().post(f"/reset-password/{tok}", data={"password": "reset99999"},
                               follow_redirects=True)
    assert b"actualizada" in r.data, "el reset por token falló"
    # token de un solo uso
    r = app.test_client().post(f"/reset-password/{tok}", data={"password": "otra99999"},
                               follow_redirects=True)
    assert b"inv\xc3\xa1lido o expirado" in r.data, "el token de reset se reusó"

    # 9. Upload: auth + magic bytes (stub del pipeline; job inline).
    import app.jobs as _jobs
    def _fake_transcribe(src, work_dir, title="", mscore_bin=None):
        p = os.path.join(work_dir, "score.musicxml")
        open(p, "w").write("<score-partwise><part/></score-partwise>")
        return p, None
    def _fake_separate_piano(audio_path, work_dir):
        p = os.path.join(work_dir, "piano.wav")
        open(p, "wb").write(b"x")
        return p
    _jobs.transcribe = _fake_transcribe
    _jobs.separate_piano_hq = _fake_separate_piano

    def up(c, data, name, extra=None):
        payload = {"audio": (BytesIO(data), name)}
        payload.update(extra or {})
        return c.post("/upload", data=payload, content_type="multipart/form-data", follow_redirects=True)

    r = up(admin, b"RIFF\x24\x08\x00\x00WAVEfmt ", "song.wav")
    assert b"no parece un audio" not in r.data, "rechazó un WAV válido"
    r = up(admin, b"<?xml version='1.0'?><x/>", "fake.wav")
    assert b"no parece un audio" in r.data, "aceptó archivo por extensión, no por contenido"
    r = app.test_client().post("/upload", data={"audio": (BytesIO(b"RIFF...WAVE.."), "x.wav")},
                               content_type="multipart/form-data")
    assert r.status_code == 302 and "/login" in r.headers["Location"], "upload sin auth permitido"
    # aislar piano (separate=1) => usa Demucs (stub) y transcribe; todo se guarda como piano
    up(admin, b"RIFF\x24\x08\x00\x00WAVEfmt ", "mix.wav", extra={"separate": "1"})
    with app.app_context():
        insts = {s.instrument for s in db.session.scalars(db.select(Score).filter_by(user_id=a_id))}
    assert insts == {"piano"}, f"la app debería producir solo piano; hay {insts}"

    # 9b. Upload MIDI: valida magic bytes (MThd) y requiere auth.
    r = admin.post("/upload-midi", data={"midi": (BytesIO(b"noesmidi...."), "x.mid")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert b"no parece un MIDI" in r.data, "aceptó un MIDI inválido por su extensión"
    r = app.test_client().post("/upload-midi", data={"midi": (BytesIO(b"MThd\x00\x00\x00\x06"), "x.mid")},
                               content_type="multipart/form-data")
    assert r.status_code == 302 and "/login" in r.headers["Location"], "upload-midi sin auth permitido"

    # 10. Ingesta por URL: allowlist en la ruta (§6.4), sin crear job.
    with app.app_context():
        jobs_before = db.session.scalar(db.select(db.func.count()).select_from(Job))
    r = admin.post("/ingest", data={"url": "http://169.254.169.254/latest/meta"}, follow_redirects=True)
    assert b"no permitido" in r.data, "no rechazó dominio fuera de la allowlist"
    with app.app_context():
        assert jobs_before == db.session.scalar(db.select(db.func.count()).select_from(Job))
    r = app.test_client().post("/ingest", data={"url": "https://youtu.be/x"})
    assert r.status_code == 302 and "/login" in r.headers["Location"], "ingest sin auth permitido"

    # 11. IDOR: B no accede a las partituras/jobs de A.
    beto = app.test_client()
    login(beto, "b@x.com", "clave1234")
    with app.app_context():
        sid = db.session.scalar(db.select(Score.id).filter_by(user_id=a_id))
        j = Job(user_id=a_id, status="queued")
        db.session.add(j)
        db.session.commit()
        jid = j.id
    assert beto.get(f"/score/{sid}").status_code == 404, "IDOR: B vio la partitura de A"
    assert beto.get(f"/score/{sid}/musicxml").status_code == 404, "IDOR: B bajó el MusicXML de A"
    assert beto.post(f"/score/{sid}/delete").status_code == 404, "IDOR: B borró la de A"
    assert beto.post(f"/job/{jid}/cancel").status_code == 404, "IDOR: B canceló el job de A"
    assert beto.get(f"/job/{jid}/status").status_code == 404, "IDOR: B vio el job de A"
    with app.app_context():
        assert db.session.get(Score, sid) is not None and db.session.get(Job, jid).status == "queued"

    # 12. Admin: gate + promover. B (normal) => 403; A (admin) => 200 y puede promover a B.
    assert beto.get("/admin").status_code == 403, "un usuario normal accedió a /admin"
    assert admin.get("/admin").status_code == 200, "el admin no accede a /admin"
    with app.app_context():
        b_id = db.session.scalar(db.select(User.id).filter_by(email="b@x.com"))
    admin.post(f"/admin/user/{b_id}/promote", follow_redirects=True)
    with app.app_context():
        assert db.session.get(User, b_id).is_admin, "el admin no pudo promover a B"

    print("OK: registro/2FA/perfil + upload + IDOR + jobs + admin (todas las aserciones)")


if __name__ == "__main__":
    run()
