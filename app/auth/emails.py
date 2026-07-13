"""Mails transaccionales (solo reseteo de contraseña). El envío va por el mailer SMTP
configurado por el admin; si no hay SMTP configurado, es no-op."""
from flask import url_for

from .. import mailer

RESET_TTL_MINUTES = 60  # expiración corta para reseteo (threat model §6.30)


def send_reset_email(user, token):
    link = url_for("auth.reset_password", token=token, _external=True)
    mailer.send(
        user.email,
        "Reseteo de contraseña de Clavis",
        f"Pediste resetear tu contraseña. Entrá a este link (válido 1h, un solo uso):\n\n"
        f"{link}\n\nSi no fuiste vos, ignorá este mail.\n",
    )
