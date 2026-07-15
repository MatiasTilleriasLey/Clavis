from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import SubmitField

from ..audio import ALLOWED

VIDEO_ALLOWED = ("mp4", "mov", "m4v", "webm", "mkv")


class UploadForm(FlaskForm):
    # FileAllowed filtra por extensión (barato); los magic bytes se validan en la ruta.
    audio = FileField("Audio", validators=[
        FileRequired(),
        FileAllowed(ALLOWED, "Formato no soportado (MP3/WAV/M4A/MP4)."),
    ])
    # Opcional: video del teclado para corregir onset/offset por visión. Sin FileRequired =>
    # FileAllowed no valida nada si no se adjuntó archivo.
    video = FileField("Video del teclado", validators=[
        FileAllowed(VIDEO_ALLOWED, "Video no soportado (MP4/MOV/WEBM/MKV)."),
    ])
    submit = SubmitField("Subir")
