"""Check runnable del flujo de auth. Corre con: .venv/bin/python -m pytest test_auth.py
o directamente .venv/bin/python test_auth.py (usa asserts, sin framework obligatorio)."""
import os
import re

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

    print("OK: flujo de auth + reset verificado (12 aserciones de seguridad)")


if __name__ == "__main__":
    run()
