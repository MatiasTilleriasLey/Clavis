# Clavis

Convierte audio (archivo o link de YouTube/Instagram/TikTok) en **partitura editable**
(MusicXML renderizado en el navegador con OpenSheetMusicDisplay) y **PDF descargable**,
con **separación de instrumentos** (Demucs) para obtener partituras por instrumento.
App **multiusuario**, pensada para correr en **red local / VPN privada**.

Proyecto personal de Matías Tillerías Ley. Seguridad como requisito de diseño desde el
día 1 — ver `THREAT_MODEL.md` antes de tocar cualquier endpoint.

## Cómo funciona

```
Landing pública → Registro (verificación por email) / Login
  → subir audio o pegar link → [Demucs separa instrumentos, opcional]
  → basic-pitch (audio→MIDI) → music21 (MIDI→MusicXML) → OSMD + PDF (MuseScore)
  → partituras guardadas por usuario. El audio original se descarta tras procesar.
```

Los jobs pesados corren en background (RQ/Redis, un worker = máx un job pesado a la vez,
porque no hay GPU dedicada). Se pueden cancelar.

## Stack

Flask · PostgreSQL · Redis + RQ · Argon2 (auth) · Flask-Login/WTF/Limiter/Mail ·
Demucs (PyTorch) · basic-pitch (ONNX) · music21 · MuseScore 4 CLI · yt-dlp · ffmpeg ·
OpenSheetMusicDisplay.

## Setup (desarrollo)

Requiere Docker, Python 3.12, ffmpeg.

```bash
# 1. Servicios (Postgres, Redis, Mailpit para ver los mails de dev)
docker compose up -d

# 2. Entorno Python + deps web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Deps ML pesadas (basic-pitch/ONNX, Demucs/torch, MuseScore, yt-dlp)
scripts/install_ml.sh
#    Copiá el MSCORE_BIN que imprime al final a tu .env.

# 4. Config
cp .env.example .env
python -c "import secrets; print('SECRET_KEY='+secrets.token_hex(32))"   # pegalo en .env
scripts/gen_cert.sh                                                       # TLS autofirmado

# 5. Base de datos
export FLASK_APP=wsgi.py
.venv/bin/flask db upgrade

# 6. Correr (dos procesos)
.venv/bin/python wsgi.py       # web en https://127.0.0.1:8443 (cert autofirmado)
.venv/bin/python worker.py     # worker de transcripción (un solo proceso)
```

Mailpit (mails de dev): http://127.0.0.1:8027 · Para hacer admin a un usuario:
`.venv/bin/flask make-admin <email>`.

Para SMTP real, cambiá las variables `MAIL_*` del `.env` (no hay que tocar código).

## Tests

```bash
export SECRET_KEY=x DATABASE_URL=sqlite:// REDIS_URL=redis://localhost:6379/0
.venv/bin/python test_auth.py      # auth, IDOR, jobs, admin (29 aserciones)
.venv/bin/python test_audio.py     # sniffer de magic bytes
.venv/bin/python test_ingest.py    # allowlist de dominios (anti-SSRF)
.venv/bin/python test_pipeline.py  # audio→MusicXML (necesita deps ML)
.venv/bin/python test_pdf.py       # MusicXML→PDF (necesita MuseScore)
```

## Seguridad

Diseñado para **LAN/VPN privada**, no para internet público. Si lo exponés a internet sin
ajustar nada, heredás los riesgos "fuera de alcance" del `THREAT_MODEL.md`. Reporte de
vulnerabilidades: ver `SECURITY.md`.

## Licencia

**GPLv3** (`LICENSE`). Compatible con MuseScore CLI (GPLv3). Dependencias principales y sus
licencias: yt-dlp (Unlicense), Demucs (MIT), basic-pitch (Apache-2.0), music21 (BSD/LGPL),
Flask y ecosistema (BSD), OpenSheetMusicDisplay (BSD-3), PyTorch (BSD).
