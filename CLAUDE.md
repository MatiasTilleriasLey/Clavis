# CLAUDE.md — Clavis

## Contexto del proyecto

**Clavis** es una app **multiusuario, open source, desplegada solo en red local/VPN** (familia + equipo SecureHex, no expuesta a internet público), con landing page pública (dentro de la VPN) que presenta las funciones y botones de Registro/Login, que convierte audio (upload directo o link de YouTube/Instagram/TikTok) en partitura editable (MusicXML renderizado en navegador) y PDF descargable, con **separación de instrumentos** (Demucs) para obtener partituras individuales por instrumento (ej. piano y guitarra por separado). Las partituras generadas **se guardan asociadas a cada usuario**; el audio/video original se descarta después de procesar (nunca se persiste).

Este es un proyecto personal de Matías Tillerías Ley, no de SecureHex — de ahí que el nombre no lleve el prefijo "Hex" de sus otros proyectos. Al ser **open source**, cualquiera puede clonar, correr, y potencialmente contribuir al repo — eso agrega algunas consideraciones nuevas (ver sección de "Open source" más abajo) que no aplicaban a los proyectos internos de SecureHex.

Autor: Matías Tillerías Ley. Este proyecto se construye con **seguridad como requisito de diseño desde el día 1**, no como parche posterior. El threat model completo está en `THREAT_MODEL.md` en este mismo repo — leerlo antes de implementar cualquier endpoint.

**Regla de oro:** ahora que es multiusuario y con datos persistentes, el riesgo más importante ya no es "un extraño en internet" sino **fallas de autorización entre usuarios de la misma VPN** (IDOR, listados sin filtrar por usuario) y **gestión de credenciales/sesión**. Cada endpoint nuevo que toque partituras debe preguntarse primero: *¿estoy verificando que este recurso pertenece al usuario de la sesión actual?* Evaluar contra `THREAT_MODEL.md` antes de mergear, especialmente las secciones 4.7 y 4.8.

---

## Stack

- **Backend:** Flask (Python)
- **Cola de jobs:** Redis + RQ (o Celery) — necesario para poder cancelar transcripciones en progreso y limitar cuántos jobs pesados (Demucs) corren en simultáneo, dado que solo hay GPU integrada
- **Base de datos:** PostgreSQL (usuarios, metadata de partituras) — preferible a SQLite dado que es multiusuario con escrituras concurrentes
- **Auth:** Flask-Login (o equivalente) para sesiones + `argon2-cffi` para hashing de contraseñas + Flask-Limiter para rate limiting en login/registro
- **Email:** SMTP (Flask-Mail o `smtplib` directo) para verificación de registro y reseteo de contraseña
- **Migraciones:** Alembic
- **Ingesta de audio:** upload directo (MP3/WAV/M4A/MP4) + yt-dlp para YouTube/Instagram/TikTok
- **Normalización:** ffmpeg
- **Separación de instrumentos:** Demucs (`htdemucs_6s`, Meta/PyTorch, open source, local, **CPU/GPU integrada — sin GPU dedicada**) — separa en 6 stems: voz, batería, bajo, guitarra, piano, otros
- **Audio → MIDI:** basic-pitch (Spotify, open source, local) — se aplica a cada stem seleccionado por el usuario
- **MIDI → MusicXML:** music21
- **MusicXML → PDF/PNG:** MuseScore 4 CLI (headless, `mscore`)
- **Frontend:** HTML/JS + OpenSheetMusicDisplay (OSMD) para render interactivo de la partitura, landing page pública + páginas de registro/login/dashboard
- **Storage de archivos:** filesystem local, organizado por `user_id`, servido siempre a través de endpoints autenticados (nunca estático directo)
- **Persistencia:** partituras (MusicXML + PDF) asociadas a cada usuario, **retención indefinida por defecto** con opción de borrado manual; el audio/video original **se borra tras el procesamiento**, nunca se persiste

---

## Open source: consideraciones adicionales

- **Licencia:** **GPL** (definido). Con esta elección desaparece la duda de compatibilidad que existía si se optaba por una licencia permisiva (MIT/Apache-2.0) — GPL es compatible de sobra con invocar MuseScore CLI (también GPLv3). Sigue valiendo la pena listar las licencias de todas las dependencias (yt-dlp, Demucs, basic-pitch, music21, Flask, etc.) en el README/NOTICE para claridad, pero ya no hay tensión de licencias que resolver.
- **Secrets nunca en el repo:** credenciales SMTP, `SECRET_KEY` de Flask, credenciales de DB — todo vía `.env` (con `.env.example` sin valores reales committeado) o variables de entorno, con `.gitignore` correcto desde el primer commit.
- **`SECURITY.md`:** archivo estándar de proyectos open source con instrucciones de cómo reportar vulnerabilidades de forma privada (ej. email de contacto) en vez de abrir un issue público.
- **Revisión de PRs externos:** si en algún momento se aceptan contribuciones de terceros, cualquier cambio que toque subprocess, autenticación, o autorización (secciones 4.3, 4.7, 4.8 del threat model) requiere revisión extra cuidadosa — es la superficie más sensible del proyecto.
- **CI/CD:** si se configura GitHub Actions u otro CI, nunca exponer secrets de producción en workflows que corren sobre PRs de forks externos.
- **Documentación del alcance de seguridad:** dejar claro en el README que el diseño asume despliegue en LAN/VPN privada — si alguien clona el repo y lo expone a internet público sin revisar el threat model, hereda todos los riesgos marcados como "fuera de alcance" en este documento (rate limiting más agresivo, TLS con CA válida, etc.)

---

## Requisitos de seguridad (no negociables)

Estos requisitos vienen directo del threat modeling — implementarlos como parte del diseño base, no dejarlos para después:

### Red / servidor
- [ ] Servidor accesible solo dentro de la VPN/red local — no exponer el puerto fuera de ese perímetro
- [ ] **TLS habilitado incluso dentro de la VPN** (certificado autofirmado o de CA interna) — requisito para poder usar cookies `Secure`
- [ ] `DEBUG=False` en todo momento — sin stack traces al cliente
- [ ] Validar header `Origin`/`Referer` en cada POST de estado
- [ ] CSRF token atado a la sesión autenticada, validado server-side en cada request que modifique estado

### Landing page pública (dentro de la VPN)
- [ ] Sin información técnica sensible expuesta (versiones de librerías, comentarios de debug, stack traces)
- [ ] Botones de Registro y Login claros, sin necesidad de autenticación para verla

### Autenticación y sesiones
- [ ] Contraseñas hasheadas con **Argon2id** (o bcrypt costo ≥12) — nunca texto plano ni hashing propio
- [ ] Rate limiting en `/login` y `/register` con backoff progresivo (Flask-Limiter) — **más importante todavía ahora que el registro es autoservicio**
- [ ] Cookies de sesión `HttpOnly`, `Secure`, `SameSite=Strict`
- [ ] Regenerar session ID tras login exitoso (previene session fixation)
- [ ] Respuestas uniformes en registro/login independiente de si el email/usuario existe (evitar enumeración)
- [ ] **Registro autoservicio con verificación de email obligatoria** — el usuario se crea la cuenta, pero no puede usarla hasta hacer click en el link de verificación enviado por SMTP. Esto es el control principal dado que no hay aprobación manual de admin.
- [ ] **Reseteo de contraseña vía SMTP**: token de un solo uso, con expiración corta (ej. 30-60 min), enviado solo al email registrado, invalidado tras el primer uso
- [ ] Rate limiting específico en `/forgot-password` (además del genérico) para evitar email bombing hacia un usuario objetivo
- [ ] Credenciales SMTP en variables de entorno / secrets manager, nunca hardcodeadas en el repo
- [ ] Sanitizar cualquier dato insertado en el cuerpo/asunto del email (nombre de usuario, etc.) para evitar header injection en el mensaje SMTP

### Autorización / aislamiento multiusuario (el requisito más crítico de esta versión)
- [ ] **Toda query que lea/modifique una partitura filtra por `user_id` de la sesión activa** — sin excepción, sin "confiar" en que el ID sea difícil de adivinar
- [ ] Endpoints de listado (`/api/scores`) filtran server-side por usuario, nunca devuelven todo y filtran en el frontend
- [ ] Archivos de partitura (PDF/MusicXML) se sirven **siempre** vía endpoint autenticado que valida ownership — nunca como estáticos en rutas predecibles
- [ ] Definir rol de admin explícito (para gestionar usuarios, ver el estado de la cola de jobs) — no hardcodear permisos especiales en cuentas normales
- [ ] Endpoints de cancelación de job (`/job/<id>/cancel`) también verifican ownership — un usuario no debe poder cancelar el job de otro
- [ ] Escribir al menos un test/caso manual que intente acceder a una partitura de otro usuario (IDOR) antes de dar por cerrado el feature de guardado

### Ingesta de URL (yt-dlp) y límites de duración
- [ ] Allowlist estricto de dominios (youtube.com, youtu.be, instagram.com, tiktok.com + CDNs conocidos) validado **antes** de invocar yt-dlp, no delegado a yt-dlp
- [ ] yt-dlp invocado con lista de argumentos (`shell=False`), nunca concatenación de strings
- [ ] Pinear versión de yt-dlp y documentar proceso de actualización periódica (se actualiza seguido por cambios en extractors)
- [ ] **Límite blando de 15 min por defecto**: si el audio/video excede eso, mostrar advertencia explícita ("esta canción dura más de 15 min, ¿seguro que querés continuar? puede degradar el rendimiento del sistema") que el usuario debe aceptar antes de continuar
- [ ] **Importante:** esa confirmación es una medida de UX, no de seguridad — es controlada por el propio usuario, así que **no reemplaza un límite duro server-side**. Definir un tope absoluto (ej. 60 min) que ni con confirmación se puede exceder, para evitar que un usuario (malicioso o simplemente descuidado) tire abajo el servidor con un archivo de 5 horas
- [ ] `timeout` duro en el subprocess de descarga, independiente del límite de duración del contenido

### Cola de jobs y cancelación
- [ ] Los jobs de procesamiento (especialmente Demucs) corren en background vía Redis/RQ, no bloqueando el request HTTP
- [ ] Endpoint para cancelar un job en progreso, verificando ownership (ver sección de autorización)
- [ ] **Limitar concurrencia de jobs pesados** (ej. máximo 1-2 Demucs corriendo a la vez) dado que no hay GPU dedicada — sin este límite, dos o tres usuarios lanzando transcripciones al mismo tiempo pueden saturar la máquina para todos, incluso sin mala intención de nadie
- [ ] Mostrar al usuario el estado del job (en cola / procesando / listo / cancelado) en el frontend

### Subprocess en general (yt-dlp, ffmpeg, MuseScore CLI)
- [ ] Nunca `shell=True`
- [ ] Nombres de archivo siempre generados internamente (UUID) — nunca derivados de metadata externa (título de video, nombre de artista, etc.)
- [ ] `timeout` en cada llamada a subprocess
- [ ] Validar magic bytes del archivo subido, no solo la extensión

### Filesystem
- [ ] `tempfile.mkdtemp()` con permisos 0700, nunca rutas/nombres predecibles
- [ ] Cleanup garantizado (context manager o `try/finally`) al terminar cada job, incluso si falla a mitad de camino

### Logging
- [ ] **No se guarda log local del contenido transcrito** (qué canción, qué link, etc.) — decisión explícita de privacidad
- [ ] Sí se pueden loguear eventos técnicos sin contenido (ej. "job X falló con error Y", timestamps, user_id) para debugging operacional, pero nunca el link/nombre de archivo/título de la canción

### Frontend / XSS
- [ ] Cualquier metadata externa (título de video, artista) se renderiza con `textContent`, nunca `innerHTML`
- [ ] Si se cargan libs JS desde CDN (OSMD u otras), usar Subresource Integrity (SRI)

### Separación de instrumentos (Demucs)
- [ ] **No hay GPU dedicada (solo integrada)** — Demucs va a ser notablemente lento; comunicar tiempos de espera esperables en la UI (ej. barra de progreso, no solo un spinner)
- [ ] UI con checkboxes por instrumento (voz/batería/bajo/guitarra/piano/otros) + botón "Extraer todos" — el usuario elige cuáles procesar con basic-pitch después de la separación
- [ ] `timeout` dedicado para el subprocess/llamada de Demucs, más generoso que el de ffmpeg pero igual de obligatorio
- [ ] Verificar que la descarga inicial de los pesos pre-entrenados sea sobre HTTPS desde el repo oficial, y cachearlos localmente para no depender de red en cada job
- [ ] Cada stem generado usa nombre interno tipo UUID (nunca derivado de metadata externa), mismo criterio que el resto del pipeline
- [ ] Limpiar los stems WAV (sin comprimir, pesados) apenas se haya generado su MIDI/MusicXML correspondiente — no esperar al cleanup final del job
- [ ] Considerar límite de espacio en disco temporal por job, dado que 6 stems sin comprimir pueden pesar varios cientos de MB

### Parsers XML (preventivo)
- [ ] Si en algún momento se agrega import de MusicXML externo, deshabilitar resolución de entidades externas por defecto desde ya (no esperar a implementar esa función para pensarlo)

---

## Flujo funcional

```
1. Visitante entra a la landing page (pública dentro de la VPN) → botones Registro / Login
2. Registro autoservicio → email de verificación (SMTP) → cuenta activa recién tras confirmar
   O Login → sesión autenticada
3. Usuario autenticado sube audio O pega link (YouTube/IG/TikTok)
4. [si es link] Validar dominio contra allowlist → si excede 15 min, mostrar advertencia y pedir
   confirmación explícita (con tope absoluto server-side igual, ej. 60 min, no evadible)
5. yt-dlp descarga (con límites) → audio local
6. ffmpeg normaliza el audio
7. Demucs separa el audio en los 6 stems (job en background, cancelable, con límite de concurrencia)
8. Usuario elige qué stem(s) procesar (checkboxes por instrumento + botón "Extraer todos")
9. basic-pitch convierte cada stem seleccionado → MIDI individual
10. music21 limpia/cuantiza cada MIDI → exporta MusicXML individual
11. Frontend: OSMD renderiza cada partitura de forma interactiva (selector/pestañas por instrumento)
12. [opcional] MuseScore CLI headless genera PDF/PNG descargable por instrumento
13. Partitura(s) se guardan en DB/storage asociadas al `user_id`, retención indefinida —
    el audio/video original se borra siempre
14. Usuario puede volver más tarde, ver/descargar sus partituras guardadas, o borrarlas manualmente
15. Cleanup de archivos temporales del pipeline (incluidos los stems intermedios)
```

---

## Orden de implementación sugerido

1. Setup base Flask + TLS autofirmado + esqueleto de DB (PostgreSQL + Alembic) + Redis (para la cola de jobs)
2. **Auth primero:** registro autoservicio, verificación por email (SMTP), login, hashing Argon2id, sesiones seguras, rate limiting — antes de tocar cualquier feature de procesamiento
3. Reseteo de contraseña vía SMTP (token de un solo uso, expiración corta)
4. Landing page pública + páginas de registro/login/dashboard vacío
5. Upload de archivo + validación de magic bytes + límites de tamaño (endpoint ya autenticado)
6. Pipeline audio → MIDI (basic-pitch) sobre archivo subido, sin yt-dlp ni Demucs todavía (validar el flujo simple primero)
7. MIDI → MusicXML (music21) + render con OSMD en frontend
8. MuseScore CLI headless → export PDF
9. **Persistencia de partituras por usuario** (retención indefinida) + endpoint de listado/descarga/borrado con verificación de ownership (probar IDOR manualmente acá antes de seguir)
10. Cola de jobs (RQ/Redis) con cancelación y límite de concurrencia — base necesaria antes de sumar Demucs
11. Integrar Demucs: separación en stems + UI de selección de instrumentos (+ botón "Extraer todos") + pipeline por stem
12. Recién ahí: ingesta de yt-dlp con allowlist + advertencia de duración (15 min) + tope duro server-side (60 min) + subprocess seguro
13. Hardening final: revisar checklist de seguridad completo contra `THREAT_MODEL.md`, con foco especial en las secciones 4.7 (auth) y 4.8 (multi-tenancy)

---

## Decisiones cerradas (definidas por el usuario, julio 2026)

| Tema | Decisión |
|---|---|
| Selección de instrumentos | El usuario elige qué stems procesar vía checkboxes, con botón "Extraer todos" |
| GPU | No hay GPU dedicada, solo integrada — el pipeline (especialmente Demucs) debe asumir hardware limitado |
| Cancelación de jobs | Sí, permitida (con verificación de ownership) |
| Límite de duración | Advertencia blanda a los 15 min (el usuario debe confirmar para continuar) + tope duro server-side no evadible (propuesto: 60 min, ver pendiente abajo) |
| Logs de contenido transcrito | No se guardan — privacidad por defecto. Solo logs técnicos sin contenido |
| Retención de partituras | Indefinida por defecto, con opción de borrado manual por el usuario |
| Registro | Autoservicio, con verificación de email obligatoria vía SMTP |
| Reseteo de contraseña | Vía SMTP (token de un solo uso, expiración corta) |
| Certificado TLS | Autofirmado por ahora |
| Licencia | GPL |

## Decisiones abiertas (a resolver antes o durante implementación)

- ¿Qué instrumentos soporta basic-pitch bien vs. mal en la práctica? — esto no requiere decisión previa, se define probando una vez que el pipeline básico esté andando; sirve para calibrar qué instrumentos "anunciar" como soportados en la landing
- **Valor exacto del tope duro de duración** (propuse 60 min como default razonable — ¿te sirve o preferís otro número?)
- **Proveedor/configuración SMTP**: ¿usás un servicio externo (ej. una cuenta Gmail con app password, SendGrid, etc.) o vas a levantar un mail server propio en la LAN? Define credenciales y configuración concreta
- Política de expiración exacta del token de reseteo de contraseña (propuse 30-60 min como rango razonable)
- ¿Límite de concurrencia de jobs pesados (Demucs)? Propuse 1-2 simultáneos dado que no hay GPU — ¿te parece razonable para el uso esperado (familia + equipo)?

---

## Fuera de alcance (por ahora)

- Exposición a internet público — si esto cambia en el futuro, el threat model completo debe rehacerse (TLS con CA válida ya no autofirmado, rate limiting más agresivo, protección anti-bot en registro, 2FA a considerar, política de contraseñas más estricta)
- Retención del audio/video original descargado — se borra siempre tras el procesamiento
- Autenticación de dos factores (2FA) — no descartada a futuro, pero no es requisito para esta fase dado el perímetro VPN
- Roles granulares más allá de usuario normal / admin
