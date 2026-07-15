<div align="center">

# 🎼 Clavis

**De audio de piano a partitura editable, en tu propia red.**

Clavis convierte audio de **piano** —un archivo o un link de YouTube / Instagram / TikTok— en una
**partitura editable** en gran pentagrama (renderizada en el navegador, reproducible, y descargable
en PDF, MusicXML y MIDI). Usa un modelo de transcripción especializado en piano y, opcionalmente,
puede **aislar el piano** de una canción con banda completa. Multiusuario, open source y pensado
para correr en **red local / VPN privada**.

![License](https://img.shields.io/badge/license-GPLv3-blue)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-RQ-DC382D?logo=redis&logoColor=white)
![PyTorch](https://img.shields.io/badge/Demucs-PyTorch-EE4C2C?logo=pytorch&logoColor=white)
![Deploy](https://img.shields.io/badge/deploy-LAN%2FVPN-success)
![Security](https://img.shields.io/badge/security-by%20design-brightgreen)

</div>

---

## Tabla de contenidos

- [¿Qué hace?](#qué-hace)
- [Cómo funciona (flujo)](#cómo-funciona-flujo)
- [Herramientas y por qué cada una](#herramientas-y-por-qué-cada-una)
- [Modelo de seguridad](#modelo-de-seguridad)
- [Instalación](#instalación)
- [Puesta en marcha](#puesta-en-marcha)
- [Uso](#uso)
- [Administración](#administración)
- [Tests](#tests)
- [Licencia](#licencia)

---

## ¿Qué hace?

- **Ingesta de audio** por dos vías: subida de archivo (MP3 / WAV / M4A / MP4) o pegado de un
  link de YouTube, Instagram o TikTok (se descarga solo el audio).
- **Transcripción de piano** con un modelo especializado (ByteDance, estado del arte en piano
  solo): detecta las notas, la **tonalidad** (aplica la armadura), y el **tempo** (♩ = BPM). La
  partitura se escribe en **gran pentagrama** (clave de sol + clave de fa).
- **Aislar el piano** (opcional): para canciones con banda completa, separa el piano de la mezcla
  con una **cascada de modelos** (MelBand Roformer quita la voz + Demucs extrae el piano) antes
  de transcribir, priorizando calidad sobre velocidad.
- **Transcripción asistida por video** (opcional): si subís un **video del teclado** junto al audio
  (cámara fija, teclado a la vista, mismo take), o marcás *"usar el video"* al pegar un **link**
  (se descarga el video además del audio, así van sincronizados por construcción), se analiza por
  visión computacional (OpenCV, sin GPU) para corregir el **onset, offset y duración** de cada
  nota. Es el punto flojo
  de transcribir solo por audio: el modelo no distingue "la tecla sigue abajo" de "el sonido sigue
  por el pedal", y estira las notas. El video sí ve cuándo se soltó la tecla. Fusión: el audio
  manda en pitch/velocity y cubre lo que la mano tapa; el video manda en los tiempos. La partitura
  llega al editor ya corregida.
- **Salidas**: render interactivo en el navegador (OpenSheetMusicDisplay), **reproducción con
  soundfont de piano** en el navegador, y descargas en **PDF**, **MusicXML** y **MIDI**.
- **Editor de notas (piano-roll tipo DAW)**: corregí la transcripción sin salir del navegador —
  agregar / mover / **dividir** / **fusionar** / redimensionar notas (a la grilla o libre), editar
  el **volumen** (velocity) y el **pedal de sustain**, cuantizar, **mutear / solo**, transponer,
  zoom, deshacer/rehacer. Al guardar se **regenera** la partitura, el PDF, el MIDI y el modo piano.
- **Modo piano (estilo Synthesia)**: una vista con las notas cayendo sobre un teclado, en una
  pestaña aparte, para aprender la canción a tu ritmo.
- **Subí tu propio MIDI**: obtené la partitura, escuchalo con sonido de piano y usá el modo piano
  y el editor sobre un MIDI que ya tengas.
- **Metadata editable**: nombre de la canción (por defecto el del archivo/video), autor y arreglo.
- **Multiusuario**: cada partitura queda asociada a su usuario. El audio/video original **se
  descarta** tras procesar — nunca se persiste.
- **Cola de trabajos** con progreso por etapa en vivo, cancelación, y notificación por email
  cuando la partitura está lista (si hay SMTP configurado).
- **Cuentas**: registro con nombre/email/contraseña (sin verificación por email en despliegue
  local), login, perfil (cambiar email/contraseña, activar **2FA/TOTP**), y rol **admin**.

## Cómo funciona (flujo)

```
Landing pública (dentro de la VPN)
        │  Registro (nombre, email, contraseña)  /  Login  (+ 2FA opcional)
        ▼
Dashboard  ──► subir archivo  ó  pegar link (YouTube/IG/TikTok)  ó  subir un MIDI
        │            │
        │            ├─ [link] validar dominio (allowlist) → yt-dlp descarga solo audio, o el
        │            │         video entero (720p, H.264) si pediste usar el video
        │            │         (tope duro 60 min server-side; aviso a los 15 min)
        │            ▼
        │      ffmpeg normaliza el audio
        │            │
        │            ├─ [opción "aislar piano"] cascada Roformer + Demucs → stem de piano
        │            ▼
        │      piano_transcription_inference (audio → MIDI)
        │            │
        │            ├─ [si subiste un video del teclado] OpenCV lo analiza y corrige
        │            │         onset/offset (el video ve soltar la tecla; el pedal, no)
        │            ▼
        │      music21/librosa (tonalidad, tempo, gran pentagrama, metadata)
        │            →  MuseScore (MusicXML + PDF)
        ▼
Partitura guardada por usuario  ──►  ver (OSMD) · reproducir (soundfont) · descargar
        │         PDF/MusicXML/MIDI · editar datos      (el audio original se borra siempre)
        ├──► Editor de notas (piano-roll) → corregir → guardar → regenera partitura/PDF/MIDI
        └──► Modo piano (Synthesia) para practicar
```

Los pasos pesados (separación, transcripción, MuseScore) corren en **background** vía Redis/RQ,
con un único worker → como mucho un trabajo pesado a la vez (la máquina no tiene GPU dedicada). El
frontend muestra la etapa actual (descargando / separando / transcribiendo / analizando el video)
y permite cancelar.
Los jobs cuyo worker muere se reconcilian al abrir el dashboard (no quedan colgados "en proceso").

## Herramientas y por qué cada una

| Componente | Herramienta | Rol |
|---|---|---|
| Backend web | **Flask** | Rutas, sesiones, plantillas |
| Base de datos | **PostgreSQL** + **Alembic** | Usuarios, partituras, jobs, settings; migraciones |
| Cola de jobs | **Redis** + **RQ** | Procesamiento en background, cancelable, con límite de concurrencia |
| Auth | **Flask-Login**, **argon2-cffi**, **Flask-WTF**, **Flask-Limiter**, **pyotp** | Sesiones, hashing Argon2id, CSRF, rate limiting, TOTP/2FA |
| Descarga por link | **yt-dlp** | Extrae el audio de YouTube/IG/TikTok (con allowlist de dominios) |
| Audio | **ffmpeg** | Normalización a WAV mono |
| Aislar piano | **MelBand Roformer** (audio-separator) + **Demucs** (`htdemucs_6s`), CPU | Cascada que quita la voz y extrae el piano de una mezcla (opcional) |
| Audio → MIDI | **piano_transcription_inference** (ByteDance, PyTorch) | Transcripción de piano SOTA |
| Video → onset/offset | **OpenCV** (CV clásica: homografía + resta de fondo) | Ve cuándo se suelta la tecla, que el pedal le esconde al audio (opcional) |
| MIDI → partitura | **music21** + **librosa** + **pretty_midi** | Tonalidad, tempo, gran pentagrama, edición de notas/pedal → MusicXML |
| Partitura → PDF | **MuseScore 4 CLI** (headless) | Import de MIDI y export a MusicXML/PDF (incluye notación de pedal) |
| Editor de notas | Piano-roll propio (Canvas/DOM, sin dependencias) | Corrección tipo DAW: notas, volumen, pedal, dividir/fusionar, cuantizar, mutear/solo |
| Render / audio en navegador | **OpenSheetMusicDisplay** + **html-midi-player** + soundfont de piano (self-hosteados) | Partitura interactiva, modo piano y reproducción realista |
| Email | **smtplib** (config SMTP por admin) | Notificación de job listo y reseteo de contraseña |

> Todas las dependencias son open source. Licencias principales: yt-dlp (Unlicense),
> Demucs (MIT), piano_transcription_inference (Apache-2.0), music21 (BSD/LGPL), Flask y
> ecosistema (BSD), OpenSheetMusicDisplay (BSD-3), PyTorch (BSD), MuseScore (GPLv3).

## Modelo de seguridad

Clavis se diseñó con **seguridad como requisito desde el día 1** (ver `THREAT_MODEL.md`). Puntos
clave:

- **Alcance: red local / VPN privada.** No está pensado para exponerse a internet público. Si lo
  hacés sin ajustar nada, heredás los riesgos "fuera de alcance" del threat model.
- **Aislamiento entre usuarios (multi-tenancy):** toda query filtra por el usuario de la sesión;
  los archivos se sirven solo por endpoints que verifican ownership (nunca como estáticos).
- **Subprocess seguro:** yt-dlp / ffmpeg / Demucs / MuseScore se invocan con lista de argumentos
  (`shell=False`), timeouts, y nombres de archivo internos (UUID) — nunca derivados de metadata
  externa.
- **Ingesta por URL:** allowlist estricta de dominios validada antes de invocar yt-dlp; tope duro
  de duración no evadible.
- **Cuentas:** contraseñas con Argon2id, rate limiting en login/registro, cookies
  `HttpOnly`/`Secure`/`SameSite=Strict`, CSRF + validación de Origin/Referer, 2FA opcional.
- **Privacidad:** el audio/video original se descarta tras procesar; no se loguea el contenido
  transcrito (links, títulos).

Reporte de vulnerabilidades: ver `SECURITY.md`.

## Instalación

**Requisitos:** Docker + Docker Compose, Python 3.12, ffmpeg, y ~2 GB de espacio para las
dependencias ML (PyTorch, MuseScore).

```bash
# 1) Servicios de infraestructura: Postgres, Redis y Mailpit (captura los mails en dev)
docker compose up -d

# 2) Entorno de Python + dependencias web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3) Dependencias ML pesadas: torch (CPU), piano_transcription_inference + su checkpoint,
#    Demucs y audio-separator (modelo MelBand Roformer) para aislar el piano, OpenCV para
#    la transcripción asistida por video, yt-dlp, MuseScore 4 y el soundfont de piano.
#    El script baja/extrae todo sin necesidad de root.
scripts/install_ml.sh
#    Copiá el MSCORE_BIN que imprime al final a tu archivo .env

# 4) Configuración
cp .env.example .env
python -c "import secrets; print('SECRET_KEY='+secrets.token_hex(32))"   # pegalo en .env
scripts/gen_cert.sh                                                       # certificado TLS autofirmado

# 5) Base de datos: aplicar migraciones
export FLASK_APP=wsgi.py
.venv/bin/flask db upgrade
```

## Puesta en marcha

Clavis corre en **dos procesos**: el servidor web y el worker de transcripción.

```bash
# Servidor web (TLS, https://127.0.0.1:8443 — certificado autofirmado, aceptá la advertencia)
.venv/bin/python wsgi.py

# Worker (un solo proceso = máximo un trabajo pesado a la vez)
.venv/bin/python worker.py
```

- App: **https://127.0.0.1:8443**
- Mailpit (mails de desarrollo): **http://127.0.0.1:8027**

## Uso

1. Entrá a la landing y **registrate** (nombre, email, contraseña). El **primer usuario que se
   registra queda como administrador**.
2. **Iniciá sesión.** Si activaste 2FA, se te pedirá el código de tu app de autenticación.
3. En el **dashboard**, subí un archivo, pegá un link o subí un MIDI. Si es una canción con banda
   completa, marcá **"Aislar el piano de la mezcla"**.
4. Seguí el **progreso** del trabajo en vivo. Al terminar, se abre la partitura.
5. **Vela** en el navegador, **escuchala** con sonido de piano y **descargala** en PDF, MusicXML o
   MIDI. Podés **editar** el nombre, autor y arreglo.
6. Si la transcripción tiene errores, abrí el **editor de notas** (piano-roll) para corregirlos:
   mover/dividir/fusionar notas, ajustar volumen y pedal, cuantizar, mutear/solo. Al guardar se
   regenera todo.
7. Practicá con el **modo piano** (estilo Synthesia), que se abre en una pestaña aparte.
8. En **Perfil** podés cambiar tu email o contraseña y activar/desactivar 2FA.

## Administración

Un administrador tiene un panel (**Admin** en la barra superior) para:

- Ver el estado de la cola de jobs y la lista de usuarios.
- **Promover** a otro usuario a administrador.
- **Configurar el SMTP** (servidor, puerto, usuario, contraseña, remitente, STARTTLS). El email
  es opcional: se usa para notificar cuando una partitura queda lista y para el reseteo de
  contraseña. Sin SMTP configurado, esas notificaciones simplemente no se envían.

Para marcar un admin desde la línea de comandos:

```bash
.venv/bin/flask make-admin <email>
```

## Tests

```bash
export SECRET_KEY=x DATABASE_URL=sqlite:// REDIS_URL=redis://localhost:6379/0
.venv/bin/python test_auth.py      # registro, 2FA, perfil, IDOR, jobs, admin
.venv/bin/python test_audio.py     # validación de magic bytes del upload
.venv/bin/python test_ingest.py    # allowlist de dominios (anti-SSRF)
.venv/bin/python test_pipeline.py  # audio → MusicXML (requiere deps ML)
.venv/bin/python test_pdf.py       # MusicXML → PDF (requiere MuseScore)
.venv/bin/python test_video.py     # transcripción visual: calibración, eventos, fusión
```

## Licencia

**GPLv3** — ver [`LICENSE`](LICENSE). Autor: Matías Tillerías Ley.
