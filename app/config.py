import os


class Config:
    """Config desde entorno. Los defaults seguros son requisito de diseño (threat model §6)."""

    SECRET_KEY = os.environ["SECRET_KEY"]  # obligatorio: falla ruidoso si falta
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # DEBUG nunca on por accidente: solo si FLASK_DEBUG=1 explícito.
    DEBUG = os.environ.get("FLASK_DEBUG") == "1"

    # Cookies de sesión endurecidas (threat model §6.20). Secure exige TLS, que ya usamos.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Strict"

    # SMTP (verificación de email + reseteo). Default = Mailpit en dev.
    # ponytail: solo config; el envío real se implementa con auth (pasos 2-3).
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "1026"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME") or None
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD") or None
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS") == "1"
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "clavis@localhost")
