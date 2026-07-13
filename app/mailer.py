"""Envío de mail con SMTP configurado por el admin (tabla Setting), no por env.
Si no está configurado, es no-op: el mail es opcional (solo notifica jobs listos y reset)."""
import smtplib
from email.message import EmailMessage

from .models import Setting

_KEYS = ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_tls")


def config():
    c = {k: Setting.get(k, "") for k in _KEYS}
    return c


def is_configured():
    c = config()
    return bool(c["smtp_host"] and c["smtp_port"] and c["smtp_from"])


def send(to, subject, body):
    """Envía si el SMTP está configurado; devuelve True/False. No lanza si no está configurado."""
    c = config()
    if not (c["smtp_host"] and c["smtp_port"] and c["smtp_from"]):
        return False
    msg = EmailMessage()
    # EmailMessage rechaza headers con saltos de línea => previene header injection (§6.32).
    msg["From"] = c["smtp_from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(c["smtp_host"], int(c["smtp_port"]), timeout=20) as s:
        if c["smtp_tls"] == "1":
            s.starttls()
        if c["smtp_username"]:
            s.login(c["smtp_username"], c["smtp_password"])
        s.send_message(msg)
    return True
