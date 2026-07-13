from functools import wraps

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)

from ..extensions import db, limiter
from ..models import EmailToken, User
from .emails import VERIFY_TTL_MINUTES, send_verification_email
from .forms import LoginForm, RegisterForm

bp = Blueprint("auth", __name__)

# Mensaje uniforme para registro: idéntico exista o no el email (anti-enumeración, §6.24).
_REGISTER_MSG = "Si el email es válido, te enviamos un link de verificación. Revisá tu correo."


def verified_required(view):
    """Login + email verificado, chequeado en cada acceso (threat model §6.29)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.email_verified:
            return redirect(url_for("auth.unverified"))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        # No revelamos si ya existe; solo creamos si es nuevo (§6.24).
        if User.query.filter_by(email=email).first() is None:
            user = User(email=email)
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.flush()  # asigna user.id para el token
            token = EmailToken.issue(user, "verify", VERIFY_TTL_MINUTES)
            db.session.commit()
            send_verification_email(user, token)
        flash(_REGISTER_MSG)
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        # Verificamos password siempre-que-exista; mensaje genérico en cualquier fallo (§6.24).
        if user is not None and user.check_password(form.password.data):
            if not user.email_verified:
                flash("Verificá tu email antes de entrar. Revisá tu correo.")
                return redirect(url_for("auth.login"))
            session.clear()  # regenera sesión tras login (anti-fixation, §6.21)
            login_user(user)
            return redirect(url_for("auth.dashboard"))
        flash("Email o contraseña incorrectos.")
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.get("/verify/<token>")
@limiter.limit("20 per hour")
def verify(token):
    user = EmailToken.consume(token, "verify")
    if user is None:
        flash("Link de verificación inválido o expirado.")
        return redirect(url_for("auth.login"))
    user.email_verified = True
    db.session.commit()
    flash("Cuenta verificada. Ya podés entrar.")
    return redirect(url_for("auth.login"))


@bp.get("/unverified")
@login_required
def unverified():
    # ponytail: sin reenvío de verificación todavía; en dev el mail llega siempre a Mailpit.
    # Agregar botón "reenviar" (con su rate limit) si hace falta en uso real.
    return render_template("auth/unverified.html")


@bp.get("/dashboard")
@verified_required
def dashboard():
    # ponytail: placeholder protegido para validar el gate de auth+verificación.
    # El dashboard real (listado de partituras) llega en pasos posteriores.
    return render_template("dashboard.html")
