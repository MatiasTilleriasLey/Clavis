import redis
from flask import Flask
from sqlalchemy import text

from .config import Config
from .extensions import db, migrate


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)

    @app.get("/health")
    def health():
        """Valida que el esqueleto realmente levanta: DB + Redis alcanzables."""
        checks = {}
        try:
            db.session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception:
            checks["db"] = "fail"
        try:
            redis.from_url(app.config["REDIS_URL"]).ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "fail"

        ok = all(v == "ok" for v in checks.values())
        return {"status": "ok" if ok else "degraded", **checks}, (200 if ok else 503)

    return app
