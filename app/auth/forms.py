from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Regexp

# CSRF token incluido automáticamente por FlaskForm (threat model §6.22).


class RegisterForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    name = StringField("Nombre", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Contraseña", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Crear cuenta")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Contraseña", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Entrar")


class TwoFactorForm(FlaskForm):
    code = StringField("Código", validators=[DataRequired(), Regexp(r"^\d{6}$", message="6 dígitos")])
    submit = SubmitField("Verificar")


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    submit = SubmitField("Enviar link de reseteo")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("Nueva contraseña", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Cambiar contraseña")


class ChangeEmailForm(FlaskForm):
    email = StringField("Nuevo email", validators=[DataRequired(), Email(), Length(max=254)])
    current_password = PasswordField("Contraseña actual", validators=[DataRequired()])
    submit = SubmitField("Cambiar email")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Contraseña actual", validators=[DataRequired()])
    new_password = PasswordField("Nueva contraseña", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Cambiar contraseña")


class TotpConfirmForm(FlaskForm):
    code = StringField("Código", validators=[DataRequired(), Regexp(r"^\d{6}$", message="6 dígitos")])
    submit = SubmitField("Activar 2FA")
