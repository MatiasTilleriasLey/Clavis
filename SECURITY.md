# Política de seguridad

## Reportar una vulnerabilidad

Si encontrás una vulnerabilidad en Clavis, **no abras un issue público** (sería un 0-day
para cualquiera que corra su propia instancia). Reportala en privado a:

**matias.tillerias@owasp.org**

Incluí, si podés: descripción, pasos para reproducir, impacto, y versión/commit afectado.
Vas a recibir acuse de recibo y coordinamos la divulgación responsable.

## Alcance de seguridad asumido

Clavis está diseñado para correr **solo en red local / VPN privada**, no expuesto a
internet público. El threat model completo (`THREAT_MODEL.md`) asume ese perímetro.

Si desplegás Clavis directamente en internet público **sin revisar el threat model**,
heredás todos los riesgos marcados como fuera de alcance en ese documento: rate limiting
más agresivo, TLS con CA válida (no autofirmado), protección anti-bot en registro,
posible 2FA, y política de contraseñas más estricta. En ese escenario, la postura de
seguridad por defecto de Clavis **no es suficiente**.

## Áreas más sensibles

Cualquier contribución que toque estas áreas requiere revisión humana cuidadosa
(ver `THREAT_MODEL.md`):

- Subprocess (yt-dlp, ffmpeg, Demucs, MuseScore) — §4.3
- Autenticación y sesiones — §4.7
- Autorización / aislamiento entre usuarios (IDOR) — §4.8
- Ingesta de URL / allowlist de dominios — §4.2
