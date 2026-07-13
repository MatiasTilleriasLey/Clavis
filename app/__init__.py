import redis
from flask import Flask, abort, redirect, render_template, request, url_for
from sqlalchemy import text

from .config import Config
from .extensions import csrf, db, limiter, login_manager, mail, migrate


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Defensa en profundidad sobre el CSRF token: rechazar POST con Origin/Referer ajeno
    # cuando venga presente (threat model §6.3). Si falta, el token CSRF sigue protegiendo.
    @app.before_request
    def check_origin():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("Origin") or request.headers.get("Referer")
            allowed = request.host_url.rstrip("/")
            if origin and origin != allowed and not origin.startswith(allowed + "/"):
                abort(403)

    from .auth import bp as auth_bp

    app.register_blueprint(auth_bp)

    @app.get("/")
    def index():
        return render_template("index.html")  # landing pública (dentro de la VPN)

    @app.get("/health")
    def health():
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
