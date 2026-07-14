import io
from functools import wraps

import pyotp
import qrcode
from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (current_user, login_required, login_user,
                         logout_user)
from qrcode.image.svg import SvgPathImage

from ..extensions import db, limiter
from ..models import EmailToken, User
from .emails import RESET_TTL_MINUTES, send_reset_email
from .forms import (ChangeEmailForm, ChangePasswordForm, ForgotPasswordForm,
                    LoginForm, RegisterForm, ResetPasswordForm, TotpConfirmForm,
                    TwoFactorForm)

bp = Blueprint("auth", __name__)

_FORGOT_MSG = "Si el email está registrado, te enviamos un link para resetear tu contraseña."


def _target_email():
    """Key para rate limiting por email destino, normalizado (threat model §6.31)."""
    return (request.form.get("email") or "").strip().lower()


def admin_required(view):
    """Rol admin explícito (§4.8). Nunca permisos hardcodeados por cuenta."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _qr_svg(data):
    img = qrcode.make(data, image_factory=SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


# --- Registro / login / logout ---

@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        # Despliegue local: sin verificación por email. Se avisa claro si el email ya existe
        # (trade-off de UX aceptado vs. anti-enumeración §4.6, dado el perímetro LAN).
        if User.query.filter_by(email=email).first() is not None:
            flash("Ese email ya está registrado.")
            return render_template("auth/register.html", form=form)
        user = User(email=email, name=form.name.data.strip())
        user.set_password(form.password.data)
        # El primer usuario del sistema queda admin por defecto (§4.8).
        if db.session.scalar(db.select(db.func.count()).select_from(User)) == 0:
            user.is_admin = True
        db.session.add(user)
        db.session.commit()
        flash("Cuenta creada. Ya podés entrar.")
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
        # Mensaje genérico en cualquier fallo (anti-enumeración en login, §6.24).
        if user is not None and user.check_password(form.password.data):
            session.clear()  # regenera sesión (anti-fixation, §6.21)
            if user.has_2fa:
                session["pending_2fa_user"] = user.id  # todavía NO autenticado
                return redirect(url_for("auth.login_2fa"))
            login_user(user)
            return redirect(url_for("auth.dashboard"))
        flash("Email o contraseña incorrectos.")
    return render_template("auth/login.html", form=form)


@bp.route("/login/2fa", methods=["GET", "POST"])
@limiter.limit("10 per 5 minutes", methods=["POST"])
def login_2fa():
    uid = session.get("pending_2fa_user")
    if not uid:
        return redirect(url_for("auth.login"))
    form = TwoFactorForm()
    if form.validate_on_submit():
        user = db.session.get(User, uid)
        if user and user.has_2fa and pyotp.TOTP(user.totp_secret).verify(form.code.data, valid_window=1):
            session.clear()
            login_user(user)
            return redirect(url_for("auth.dashboard"))
        flash("Código incorrecto.")
    return render_template("auth/login_2fa.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


# --- Reseteo de contraseña por email (funciona solo si el admin configuró SMTP) ---

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
            send_reset_email(user, token)  # no-op si no hay SMTP configurado
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
        user = EmailToken.consume(token, "reset")
        if user is None:
            flash("Link de reseteo inválido o expirado.")
            return redirect(url_for("auth.forgot_password"))
        user.set_password(form.password.data)
        EmailToken.invalidate_pending(user.id, "reset")
        db.session.commit()
        flash("Contraseña actualizada. Ya podés entrar.")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form, token=token)


# --- Perfil ---

@bp.get("/profile")
@login_required
def profile():
    return render_template("auth/profile.html",
                           email_form=ChangeEmailForm(obj=current_user),
                           pw_form=ChangePasswordForm())


@bp.post("/profile/email")
@login_required
def change_email():
    form = ChangeEmailForm()
    if not form.validate_on_submit():
        flash("Revisá el email ingresado.")
    elif not current_user.check_password(form.current_password.data):
        flash("Contraseña actual incorrecta.")
    else:
        new = form.email.data.strip().lower()
        clash = User.query.filter(User.email == new, User.id != current_user.id).first()
        if clash is not None:
            flash("Ese email ya está en uso.")
        else:
            current_user.email = new
            db.session.commit()
            flash("Email actualizado.")
    return redirect(url_for("auth.profile"))


@bp.post("/profile/password")
@login_required
def change_password():
    form = ChangePasswordForm()
    if not form.validate_on_submit():
        flash("La nueva contraseña debe tener al menos 8 caracteres.")
    elif not current_user.check_password(form.current_password.data):
        flash("Contraseña actual incorrecta.")
    else:
        current_user.set_password(form.new_password.data)
        db.session.commit()
        flash("Contraseña actualizada.")
    return redirect(url_for("auth.profile"))


# --- 2FA (TOTP) ---

@bp.post("/profile/2fa/setup")
@login_required
def twofa_setup():
    session["pending_totp"] = pyotp.random_base32()  # secreto pendiente hasta confirmar
    return redirect(url_for("auth.twofa_show"))


@bp.get("/profile/2fa")
@login_required
def twofa_show():
    secret = session.get("pending_totp")
    if not secret:
        return redirect(url_for("auth.profile"))
    uri = pyotp.TOTP(secret).provisioning_uri(name=current_user.email, issuer_name="Clavis")
    return render_template("auth/twofa_setup.html", secret=secret,
                           qr_svg=_qr_svg(uri), form=TotpConfirmForm())


@bp.post("/profile/2fa/enable")
@login_required
def twofa_enable():
    secret = session.get("pending_totp")
    form = TotpConfirmForm()
    if secret and form.validate_on_submit() and pyotp.TOTP(secret).verify(form.code.data, valid_window=1):
        current_user.totp_secret = secret
        db.session.commit()
        session.pop("pending_totp", None)
        flash("Autenticación en dos pasos activada.")
        return redirect(url_for("auth.profile"))
    flash("Código incorrecto, probá de nuevo.")
    return redirect(url_for("auth.twofa_show"))


@bp.post("/profile/2fa/disable")
@login_required
def twofa_disable():
    # Desactivar 2FA requiere la contraseña actual.
    if current_user.check_password(request.form.get("current_password", "")):
        current_user.totp_secret = None
        db.session.commit()
        flash("Autenticación en dos pasos desactivada.")
    else:
        flash("Contraseña incorrecta.")
    return redirect(url_for("auth.profile"))


@bp.get("/dashboard")
@login_required
def dashboard():
    from ..models import Job, Score
    # Reconciliar jobs fantasma (worker muerto los deja colgados en 'queued'/'started').
    try:
        from ..jobs import _redis, reap_stale
        reap_stale(current_user.id, _redis())
    except Exception:
        current_app.logger.warning("reap_stale falló", exc_info=True)
    # Listados filtrados server-side por el usuario de la sesión (§4.8).
    scores = (Score.query.filter_by(user_id=current_user.id)
              .order_by(Score.created_at.desc()).all())
    jobs = (Job.query.filter_by(user_id=current_user.id)
            .filter(Job.status.in_(("queued", "started", "failed")))
            .order_by(Job.created_at.desc()).all())
    from ..transcribers import available_engines
    engines = available_engines()   # para el <select> de motor (solo si hay más de uno)
    return render_template("dashboard.html", scores=scores, jobs=jobs, engines=engines)
