import os


class Config:
    """Config desde entorno. Los defaults seguros son requisito de diseño (threat model §6)."""

    SECRET_KEY = os.environ["SECRET_KEY"]  # obligatorio: falla ruidoso si falta
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Rate limiter persistido en Redis (threat model §6.19).
    RATELIMIT_STORAGE_URI = REDIS_URL

    # Jobs de transcripción encolados en RQ (False => inline, solo para tests).
    RQ_ASYNC = True

    # URL base para armar links en mails (el worker no tiene request context).
    BASE_URL = os.environ.get("BASE_URL", "https://127.0.0.1:8443")

    # DEBUG nunca on por accidente: solo si FLASK_DEBUG=1 explícito.
    DEBUG = os.environ.get("FLASK_DEBUG") == "1"

    # Límite duro de tamaño de upload (threat model §6.7). Werkzeug corta con 413.
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB

    # Binario de MuseScore (AppRun del AppImage extraído) para export PDF. Ver scripts/install_ml.sh.
    MSCORE_BIN = os.environ.get("MSCORE_BIN")

    # Storage de partituras (fuera de static; acceso solo por endpoints con ownership).
    STORAGE_ROOT = os.environ.get("STORAGE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "storage")

    # Cookies de sesión endurecidas (threat model §6.20). Secure exige TLS, que ya usamos.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Strict"

    # SMTP ya no vive en config: lo configura el admin en runtime (tabla Setting, app/mailer.py).
