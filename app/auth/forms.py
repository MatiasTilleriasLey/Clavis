from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length

# CSRF token incluido automáticamente por FlaskForm (threat model §6.22).


class RegisterForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Contraseña", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Registrarse")


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Contraseña", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Entrar")
