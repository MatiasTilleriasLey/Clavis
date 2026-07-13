import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from flask_login import UserMixin

from .extensions import db

# Argon2id con parámetros por defecto de la librería (threat model §6.18).
_ph = PasswordHasher()


def _now():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)

    def set_password(self, password):
        self.password_hash = _ph.hash(password)

    def check_password(self, password):
        try:
            return _ph.verify(self.password_hash, password)
        except Argon2Error:
            return False


class EmailToken(db.Model):
    """Token de un solo uso para verificación de email y reseteo de contraseña.
    En DB se guarda solo el hash del token; el valor plano viaja únicamente en el mail."""

    __tablename__ = "email_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    purpose = db.Column(db.String(16), nullable=False)  # "verify" | "reset"
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User")

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, user, purpose, ttl_minutes):
        """Crea un token, lo persiste (hasheado) y devuelve el valor plano para el mail."""
        token = secrets.token_urlsafe(32)
        db.session.add(cls(
            user_id=user.id,
            token_hash=cls._hash(token),
            purpose=purpose,
            expires_at=_now() + timedelta(minutes=ttl_minutes),
        ))
        return token

    @classmethod
    def invalidate_pending(cls, user_id, purpose):
        """Marca usados todos los tokens sin usar de ese propósito (ej. al resetear password)."""
        cls.query.filter_by(user_id=user_id, purpose=purpose, used_at=None).update({"used_at": _now()})

    @classmethod
    def consume(cls, token, purpose):
        """Valida y marca usado en un solo paso. Devuelve el User o None si inválido/expirado/usado."""
        row = cls.query.filter_by(token_hash=cls._hash(token), purpose=purpose, used_at=None).first()
        if row is None:
            return None
        # Postgres devuelve aware; SQLite pierde el tz. Guardamos UTC, así que lo asumimos.
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _now():
            return None
        row.used_at = _now()
        return row.user
