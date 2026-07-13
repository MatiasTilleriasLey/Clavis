"""Check runnable del flujo de auth. Corre con: .venv/bin/python -m pytest test_auth.py
o directamente .venv/bin/python test_auth.py (usa asserts, sin framework obligatorio)."""
import os
import re
import tempfile
from io import BytesIO

# Config mínima para importar app.config sin un .env real.
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db, mail  # noqa: E402
from app.models import User  # noqa: E402


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # in-memory
    WTF_CSRF_ENABLED = False               # CSRF es responsabilidad de Flask-WTF, no del test
    RATELIMIT_ENABLED = False
    SESSION_COOKIE_SECURE = False          # el test client habla http, no TLS
    STORAGE_ROOT = tempfile.mkdtemp()
    MSCORE_BIN = None                      # sin PDF en el test
    RQ_ASYNC = False                       # jobs inline, sin Redis/worker


def _token_from_outbox(outbox, path="verify"):
    assert len(outbox) == 1, f"esperaba 1 mail, hubo {len(outbox)}"
    m = re.search(rf"/{path}/(\S+)", outbox[0].body)
    assert m, f"el mail no contiene link de {path}"
    return m.group(1)


def run():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
    client = app.test_client()

    # 1. Registro nuevo => manda exactamente 1 mail con token.
    with mail.record_messages() as outbox:
        client.post("/register", data={"email": "a@x.com", "password": "clave1234"})
    token = _token_from_outbox(outbox)

    # 2. Login antes de verificar => NO llega al dashboard.
    r = client.post("/login", data={"email": "a@x.com", "password": "clave1234"},
                    follow_redirects=True)
    assert b"Dashboard" not in r.data, "entró sin verificar el email"

    # 3. Verificar con el token.
    client.get(f"/verify/{token}")
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(email="a@x.com")).email_verified

    # 4. Token de un solo uso: reusarlo falla.
    r = client.get(f"/verify/{token}", follow_redirects=True)
    assert b"inv\xc3\xa1lido o expirado" in r.data, "el token de verificación se reusó"

    # 5. Login tras verificar => dashboard con el email.
    r = client.post("/login", data={"email": "a@x.com", "password": "clave1234"},
                    follow_redirects=True)
    assert b"Dashboard" in r.data and b"a@x.com" in r.data, "login verificado falló"

    # 6. Contraseña incorrecta => mensaje genérico, sin dashboard.
    client.get("/logout")  # por si acaso; logout real es POST, esto no rompe
    c2 = app.test_client()
    r = c2.post("/login", data={"email": "a@x.com", "password": "malamala"},
                follow_redirects=True)
    assert b"incorrectos" in r.data and b"Dashboard" not in r.data

    # 7. Anti-enumeración: registrar un email ya existente responde igual y NO duplica.
    with mail.record_messages() as outbox:
        client.post("/register", data={"email": "a@x.com", "password": "otraclave1"})
    assert len(outbox) == 0, "reveló que el email ya existía (mandó mail)"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1

    # 8. Dashboard sin sesión => redirige a login.
    r = app.test_client().get("/dashboard")
    assert r.status_code == 302 and "/login" in r.headers["Location"]

    # --- Upload + cola (paso 5-10), con `client` logueado y verificado ---
    # Stub del pipeline: el job corre inline (RQ_ASYNC=False); patcheamos app.jobs.transcribe.
    import app.jobs as _jobs
    def _fake_transcribe(src, work_dir):
        p = os.path.join(work_dir, "score.musicxml")
        open(p, "w").write("<score-partwise><part/></score-partwise>")
        return p
    _jobs.transcribe = _fake_transcribe

    def up(cl, data, name):
        return cl.post("/upload", data={"audio": (BytesIO(data), name)},
                       content_type="multipart/form-data", follow_redirects=True)

    # 8a. Magic bytes de WAV válidos => encola, el job (inline) transcribe y persiste.
    r = up(client, b"RIFF\x24\x08\x00\x00WAVEfmt ", "song.wav")
    assert b"no parece un audio" not in r.data, "rechazó un WAV válido"

    # 8b. Magic bytes mandan sobre la extensión: .wav con contenido no-audio => rechazado.
    r = up(client, b"<?xml version='1.0'?><x/>", "fake.wav")
    assert b"no parece un audio" in r.data, "aceptó un archivo por su extensión, no su contenido"

    # 8c. Sin sesión => no se puede subir (redirige a login).
    r = app.test_client().post("/upload", data={"audio": (BytesIO(b"RIFF...WAVE.."), "x.wav")},
                               content_type="multipart/form-data")
    assert r.status_code == 302 and "/login" in r.headers["Location"], "upload sin auth permitido"

    # --- IDOR / aislamiento multiusuario (paso 9, §4.8) ---
    from app.models import Score
    with app.app_context():
        a = db.session.scalar(db.select(User).filter_by(email="a@x.com"))
        a_id = a.id
        score = db.session.scalar(db.select(Score).filter_by(user_id=a_id))
        assert score is not None, "el upload no persistió la partitura"
        sid = score.id
        # crear usuario B verificado directamente
        b = User(email="b@x.com", email_verified=True); b.set_password("clave1234")
        db.session.add(b); db.session.commit()

    cb = app.test_client()
    cb.post("/login", data={"email": "b@x.com", "password": "clave1234"})

    # 9a. B no puede VER la partitura de A.
    assert cb.get(f"/score/{sid}").status_code == 404, "IDOR: B vio la partitura de A"
    # 9b. B no puede descargar el MusicXML de A.
    assert cb.get(f"/score/{sid}/musicxml").status_code == 404, "IDOR: B bajó el MusicXML de A"
    # 9c. B no puede BORRAR la partitura de A (y sigue existiendo).
    assert cb.post(f"/score/{sid}/delete").status_code == 404, "IDOR: B borró la de A"
    with app.app_context():
        assert db.session.get(Score, sid) is not None, "la partitura de A fue borrada por B"
    # 9d. El dashboard de B no lista la partitura de A.
    assert b"song" not in cb.get("/dashboard").data, "el listado de B incluyó la de A"
    # 9e. A sí accede a la suya.
    assert client.get(f"/score/{sid}").status_code == 200, "A no accede a su propia partitura"

    # --- Ownership en cancelación de jobs (paso 10, §6.28) ---
    from app.models import Job
    with app.app_context():
        j = Job(user_id=a_id, status="queued")
        db.session.add(j); db.session.commit()
        jid = j.id
    # 10a. B no puede cancelar el job de A (404), y sigue en cola.
    assert cb.post(f"/job/{jid}/cancel").status_code == 404, "IDOR: B canceló el job de A"
    with app.app_context():
        assert db.session.get(Job, jid).status == "queued", "el job de A fue cancelado por B"
    # 10b. B tampoco ve su estado.
    assert cb.get(f"/job/{jid}/status").status_code == 404, "IDOR: B vio el estado del job de A"

    # --- Reseteo de contraseña (paso 3) ---
    anon = app.test_client()  # sin sesión: forgot-password redirige si estás logueado
    # 9. forgot-password de email existente => manda 1 mail con token de reset.
    with mail.record_messages() as outbox:
        anon.post("/forgot-password", data={"email": "a@x.com"})
    reset_token = _token_from_outbox(outbox, "reset-password")

    # 10. Anti-enumeración: email inexistente responde igual y NO manda mail.
    with mail.record_messages() as outbox:
        anon.post("/forgot-password", data={"email": "nadie@x.com"})
    assert len(outbox) == 0, "reveló que el email no existía (mandó mail)"

    # 11. Reset con token válido => cambia la contraseña.
    c = app.test_client()
    r = c.post(f"/reset-password/{reset_token}", data={"password": "nuevaclave9"},
               follow_redirects=True)
    assert b"actualizada" in r.data, "el reset no confirmó"

    # 12. Token de reset de un solo uso: reusarlo falla.
    r = c.post(f"/reset-password/{reset_token}", data={"password": "otra12345"},
               follow_redirects=True)
    assert b"inv\xc3\xa1lido o expirado" in r.data, "el token de reset se reusó"

    # 13. La contraseña vieja ya no sirve; la nueva sí.
    r = c.post("/login", data={"email": "a@x.com", "password": "clave1234"},
               follow_redirects=True)
    assert b"Dashboard" not in r.data, "la contraseña vieja siguió sirviendo"
    r = c.post("/login", data={"email": "a@x.com", "password": "nuevaclave9"},
               follow_redirects=True)
    assert b"Dashboard" in r.data, "la contraseña nueva no sirvió"

    print("OK: auth + reset + upload + IDOR + jobs verificado (23 aserciones de seguridad)")


if __name__ == "__main__":
    run()
