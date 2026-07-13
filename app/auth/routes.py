from functools import wraps

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)

from ..extensions import db, limiter
from ..models import EmailToken, User
from .emails import (RESET_TTL_MINUTES, VERIFY_TTL_MINUTES, send_reset_email,
                     send_verification_email)
from .forms import (ForgotPasswordForm, LoginForm, RegisterForm,
                    ResetPasswordForm)

bp = Blueprint("auth", __name__)

# Mensaje uniforme para registro: idéntico exista o no el email (anti-enumeración, §6.24).
_REGISTER_MSG = "Si el email es válido, te enviamos un link de verificación. Revisá tu correo."
_FORGOT_MSG = "Si el email está registrado, te enviamos un link para resetear tu contraseña."


def _target_email():
    """Key para rate limiting por email destino, normalizado (threat model §6.31)."""
    return (request.form.get("email") or "").strip().lower()


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


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])                          # por IP
@limiter.limit("3 per hour", methods=["POST"], key_func=_target_email)  # por email destino (§6.31)
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        if user is not None:
            token = EmailToken.issue(user, "reset", RESET_TTL_MINUTES)
            db.session.commit()
            send_reset_email(user, token)
        flash(_FORGOT_MSG)  # respuesta uniforme exista o no el email (§6.24)
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        # El token se valida y consume (un solo uso) recién al enviar la nueva contraseña.
        user = EmailToken.consume(token, "reset")
        if user is None:
            flash("Link de reseteo inválido o expirado.")
            return redirect(url_for("auth.forgot_password"))
        user.set_password(form.password.data)
        EmailToken.invalidate_pending(user.id, "reset")  # anula otros links de reset pendientes
        db.session.commit()
        flash("Contraseña actualizada. Ya podés entrar.")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form, token=token)


@bp.get("/unverified")
@login_required
def unverified():
    # ponytail: sin reenvío de verificación todavía; en dev el mail llega siempre a Mailpit.
    # Agregar botón "reenviar" (con su rate limit) si hace falta en uso real.
    return render_template("auth/unverified.html")


@bp.get("/dashboard")
@verified_required
def dashboard():
    from ..models import Score
    # Listado filtrado server-side por el usuario de la sesión (nunca todo + filtro en front, §4.8).
    scores = (Score.query.filter_by(user_id=current_user.id)
              .order_by(Score.created_at.desc()).all())
    return render_template("dashboard.html", scores=scores)
