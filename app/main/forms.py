from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import SubmitField

from ..audio import ALLOWED


class UploadForm(FlaskForm):
    # FileAllowed filtra por extensión (barato); los magic bytes se validan en la ruta.
    audio = FileField("Audio", validators=[
        FileRequired(),
        FileAllowed(ALLOWED, "Formato no soportado (MP3/WAV/M4A/MP4)."),
    ])
    submit = SubmitField("Subir")
