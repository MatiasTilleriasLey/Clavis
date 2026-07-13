from flask import url_for
from flask_mail import Message

from ..extensions import mail

VERIFY_TTL_MINUTES = 24 * 60  # 24h para verificación de registro


def send_verification_email(user, token):
    # url_for con _external usa el host de la request (TLS). Token plano solo en el mail.
    link = url_for("auth.verify", token=token, _external=True)
    msg = Message(
        subject="Verificá tu cuenta de Clavis",
        recipients=[user.email],
        body=f"Confirmá tu cuenta entrando a este link (válido 24h):\n\n{link}\n",
    )
    # ponytail: cuerpo de texto plano sin datos controlados por el usuario => sin riesgo
    # de header injection (threat model §6.32). Si se agrega el nombre, sanitizar acá.
    mail.send(msg)
