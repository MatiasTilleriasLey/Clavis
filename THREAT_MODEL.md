# Threat Model — Clavis (Transcriptor Local de Audio a Partitura, Open Source)

**Alcance confirmado:** aplicación **multiusuario**, desplegada solo en red local/VPN (no expuesta a internet público), con landing page pública (dentro de la VPN), registro y login de usuarios, y persistencia de partituras por usuario (el audio/video original se descarta tras el procesamiento — solo se conserva la partitura). Incluye ingesta de archivo de audio Y de links de YouTube/Instagram/TikTok, con separación de instrumentos (Demucs) previa a la transcripción por stem. **El proyecto es open source** — cualquiera puede clonar y correr Clavis, lo que agrega consideraciones de supply chain y manejo de secrets que no aplicarían a un proyecto interno cerrado (ver sección 8).

> **Nota de versión:** este documento reemplaza el modelo anterior (single-user, efímero, sin auth). El cambio de alcance activa categorías de amenaza completamente nuevas: gestión de credenciales, sesiones, y sobre todo **aislamiento de datos entre usuarios (multi-tenancy)**. Ver sección 4.6 en adelante.

**Fecha:** julio 2026
**Metodología:** STRIDE + DFD (Data Flow Diagram)

---

## 1. Resumen ejecutivo

Aunque la app corre solo en red local/VPN (no en internet público), **"red local" no es sinónimo de "confiable"**. Ahora hay múltiples usuarios (familia + equipo SecureHex) con cuentas propias y datos persistentes, lo que introduce riesgos que no existían en la versión single-user:

- Un usuario de la VPN podría intentar acceder a las partituras de otro (falla de autorización / IDOR)
- Las contraseñas deben almacenarse y gestionarse correctamente aunque el atacante "más probable" sea alguien con acceso legítimo a la red
- La superficie de sesión (cookies, tokens) ahora es un objetivo real, incluso dentro de una red confiable — dispositivos compartidos, VPN comprometida, o simple descuido (dejar sesión abierta)
- Sigue existiendo todo el riesgo heredado del pipeline de procesamiento (yt-dlp, subprocess, Demucs) descrito en las secciones 4.1–4.4, que no desaparece por agregar autenticación

El objetivo de este documento es identificar dónde puede escalar de "bug de la app" a "un usuario ve o modifica datos de otro" o "ejecución de código en el servidor".

---

## 2. Arquitectura y Data Flow Diagram (DFD)

```
[Visitante VPN] --(0) Landing page pública (info, botones Registro/Login)--> [Flask App]
      |
      | (0.5) Registro / Login --> [DB: tabla users, password hash]
      |
      v (sesión autenticada, cookie)
[Navegador]
      |  (1) HTTP :servidor-VPN:PORT (requests autenticados)
      v
[Flask App] ---(2) URL externa---> [YouTube/IG/TikTok] --> yt-dlp --> archivo audio/video
      |
      |--(3) subprocess: ffmpeg (normalización de audio)
      |
      |--(3.5) Demucs (htdemucs_6s, PyTorch) --> stems: voz/batería/bajo/guitarra/piano/otros
      |
      |--(4) basic-pitch (TensorFlow) --> MIDI, uno por cada stem seleccionado
      |
      |--(5) music21 --> MusicXML, uno por cada stem
      |
      |--(6) subprocess: MuseScore CLI (headless) --> PDF/PNG por stem
      |
      |--(7) audio/video original: borrado tras procesar
      |
      v
[DB: tabla scores (user_id, metadata)] + [Storage: MusicXML/PDF por usuario]
      |
      v
[Navegador del usuario dueño] <--- (autorización verificada por user_id en cada request)
```

### Trust boundaries identificados

| # | Boundary | Por qué importa |
|---|----------|------------------|
| TB1 | Navegador ↔ Flask (red VPN) | Ya no es "solo yo" — cualquier dispositivo conectado a la VPN puede intentar hablarle al servidor; CSRF y sesión ahora son objetivos reales |
| TB2 | Flask ↔ Internet (yt-dlp) | Contenido y metadata 100% no confiables; yt-dlp es un extractor complejo con historial de CVEs |
| TB3 | Flask ↔ subprocess (yt-dlp, ffmpeg, Demucs, MuseScore) | Punto clásico de command injection si no se usa exec con lista de args |
| TB4 | Flask ↔ filesystem temporal | Path traversal, symlink races, cleanup incompleto |
| TB5 | MusicXML/metadata ↔ Frontend (OSMD) | Si hay strings de origen externo (título de video, tags) se renderizan sin sanitizar → XSS |
| **TB6** | **Visitante anónimo ↔ Usuario autenticado** | Landing page es pública dentro de la VPN; todo lo demás (subir audio, ver partituras) debe requerir sesión válida — verificar en cada endpoint, no solo en el frontend |
| **TB7** | **Usuario A ↔ Usuario B (multi-tenancy)** | El boundary más importante de esta versión: un usuario autenticado NO debe poder leer, listar, ni modificar partituras de otro usuario, ni por URL directa ni por API |

---

## 2.1 Alcance descartado (por ahora, explícitamente)

- Exposición a internet público — si esto cambia, hay que rehacer secciones enteras (TLS obligatorio, rate limiting agresivo, protección anti-bot en registro, política de contraseñas más estricta, posible 2FA)
- Retención del audio/video original — se descarta tras procesar, lo que reduce (no elimina) el riesgo legal de copyright y el de almacenamiento de contenido potencialmente sensible

---

## 3. Activos a proteger

- Integridad y confidencialidad del filesystem del usuario (si hay RCE, el atacante tiene tu máquina, no solo la app)
- Disponibilidad de la máquina (CPU/RAM/disco no deben poder agotarse por un solo request)
- Integridad de la transcripción generada
- (Secundario, no-técnico) exposición legal por descarga de contenido con copyright vía yt-dlp

---

## 4. Análisis STRIDE por componente

### 4.1 Servidor local (TB1) — el riesgo más subestimado

| Amenaza | Detalle |
|---|---|
| **CSRF** | Sin autenticación, un sitio malicioso que el usuario visite en paralelo puede hacer `fetch('http://127.0.0.1:PORT/transcribe', {method:'POST', body:{url:'...'}})` y disparar descargas/procesamiento sin que el usuario se entere. Muchos navegadores permiten requests cross-origin a IPs privadas/localhost sin restricción especial. |
| **DNS Rebinding** | Un dominio atacante resuelve primero a IP pública (pasa same-origin checks básicos) y luego "rebinding" a 127.0.0.1, permitiendo que JS del atacante hable con tu app como si fuera su propio backend. |
| **CORS mal configurado** | Si en algún momento se agrega `Access-Control-Allow-Origin: *` "para simplificar el dev", se abre la puerta a que cualquier página lea las respuestas, no solo dispare requests. |

**Impacto real:** un atacante remoto (vía una página web cualquiera) podría forzar tu app local a descargar de una URL arbitraria (ver SSRF abajo) o consumir tus recursos, sin ningún acceso previo a tu red.

### 4.2 Ingesta de URL / yt-dlp (TB2, TB3)

| Amenaza | Detalle |
|---|---|
| **SSRF** | yt-dlp acepta URLs; si no se restringe el dominio a un allowlist (youtube.com, instagram.com, tiktok.com + sus CDNs), un CSRF (ver 4.1) podría hacer que tu app pida `http://169.254.169.254/...` o `http://localhost:otro-puerto` — pivoteo hacia otros servicios locales. |
| **Command Injection** | yt-dlp históricamente ha tenido CVEs relacionados a extractors maliciosos y opciones como `--exec`. Si se invoca vía `shell=True` con concatenación de strings, una URL craftada podría inyectar comandos. |
| **Arbitrary file write** | Algunas versiones vulnerables de yt-dlp permitían escribir subtítulos/metadata en rutas fuera del directorio esperado. |
| **DoS** | Un link a un live stream de 10 horas o una playlist completa puede agotar disco/CPU/tiempo. |
| **Supply chain** | yt-dlp se actualiza semanalmente por cambios en los extractors; una versión pineada y no actualizada acumula CVEs conocidos rápido. |

### 4.3 Procesamiento de audio (ffmpeg, basic-pitch/TensorFlow)

| Amenaza | Detalle |
|---|---|
| **Tampering / RCE vía parser** | ffmpeg y libsndfile tienen historial extenso de CVEs explotables con archivos de audio/video craftados (heap overflow, etc.). Un archivo "de música" puede ser un exploit. |
| **DoS (decompression/resource bomb)** | Archivos con metadata que declara duración/canales absurdos, o audio real de duración extrema, pueden agotar RAM en el paso de inferencia de TensorFlow. |
| **Supply chain** | TensorFlow y sus dependencias nativas son una superficie grande; pinear versiones y no cargar modelos `.pkl`/checkpoints de fuentes no confiables. |

### 4.35 Separación de instrumentos (Demucs / PyTorch)

| Amenaza | Detalle |
|---|---|
| **DoS (agravado)** | Demucs es sustancialmente más pesado que basic-pitch — sin GPU, procesar una canción puede tardar varios minutos y consumir bastante RAM. Esto **amplifica** el riesgo de DoS ya identificado en 4.1/4.2: si un CSRF logra disparar un job, ahora el costo de cada job disparado es mayor. Reforzar el límite de duración/tamaño de entrada se vuelve más crítico, no opcional. |
| **Supply chain** | Se suma PyTorch + el paquete `demucs` + los pesos del modelo pre-entrenado (descargados de un repo de Meta la primera vez que se usa). Verificar que la descarga de pesos se haga sobre HTTPS desde el repo oficial y no quede cacheada de una fuente no verificada. |
| **Multiplicación de superficie downstream** | Al generar varios stems, cada uno entra de nuevo al pipeline de basic-pitch → music21 → MuseScore CLI. Cualquier vulnerabilidad de esos componentes (ver 4.3 y 4.4) ahora se dispara N veces por job en vez de 1, y cada stem debe pasar por el mismo manejo seguro de nombres de archivo (UUID por stem, nunca "guitarra_del_video_de_fulano.mid"). |
| **Resource exhaustion en disco** | Cada stem es un archivo de audio completo (WAV, no comprimido) — 6 stems de una canción de varios minutos pueden ocupar cientos de MB temporalmente. Sumar esto al límite de espacio en disco temporal permitido por job. |

### 4.4 MIDI → MusicXML (music21) y MusicXML → PDF (MuseScore CLI)

| Amenaza | Detalle |
|---|---|
| **Parser exploits** | MIDI craftado puede explotar bugs de parsing en music21 (menos común, pero no descartable). |
| **XXE** | Si en el futuro se permite *importar* MusicXML de terceros (no solo generarlo internamente), y se parsea con `lxml`/`ElementTree` sin deshabilitar entidades externas → XXE clásico (lectura de archivos locales, SSRF). Hoy el riesgo es bajo porque el XML se genera internamente, pero **hay que dejarlo bloqueado desde ya** por si se agrega esa función después. |
| **Command Injection (MuseScore CLI)** | Si el nombre de archivo pasado a `mscore -o output.pdf input.musicxml` se arma con datos derivados del título del video (que viene de una fuente no confiable, YouTube/IG/TikTok) y se ejecuta con `shell=True`, es un vector directo de RCE. |
| **Path Traversal** | Mismo problema: si el "nombre bonito" del archivo de salida usa el título del video sin sanitizar (`../../etc/whatever`), se puede escribir fuera del directorio esperado. |

### 4.5 Filesystem temporal (TB4)

| Amenaza | Detalle |
|---|---|
| **Symlink race / TOCTOU** | Nombres de archivo temporal predecibles permiten ataques de carrera en sistemas multiusuario. |
| **Cleanup incompleto** | Si el proceso muere a mitad de una transcripción, quedan archivos de audio/PDF residuales en `/tmp` legibles por otros usuarios del sistema (menos crítico en un laptop personal de un solo usuario, pero mala práctica). |

### 4.6 Landing page pública (dentro de la VPN)

| Amenaza | Detalle |
|---|---|
| **Information disclosure** | Aunque es "solo VPN", si la landing expone detalles técnicos (versiones de librerías, stack trace en algún link roto, comentarios de debug) facilita reconocimiento a cualquiera con acceso a la VPN. Tratarla con la misma disciplina que una landing pública real. |
| **Enumeración de usuarios vía registro** | Si el formulario de registro responde distinto según si el email/username ya existe ("este correo ya está registrado" vs "cuenta creada"), permite enumerar usuarios válidos. Aplica aunque sea "solo familia y equipo" — reduce fricción para ataques de credential stuffing dirigidos. |

### 4.7 Registro, login, sesión, y verificación por email (SMTP)

**Decisión de producto:** registro autoservicio (no por invitación). Esto significa que el control principal contra cuentas no deseadas dentro de la VPN pasa a ser la **verificación de email obligatoria**, no una aprobación manual.

| Amenaza | Detalle |
|---|---|
| **Almacenamiento débil de contraseñas** | Nunca guardar contraseñas en texto plano ni con hashing débil (MD5/SHA1 sin salt). Usar Argon2id (preferido) o bcrypt con costo adecuado. |
| **Fuerza bruta / credential stuffing** | Sin rate limiting en `/login`, cualquier dispositivo de la VPN (o uno comprometido) puede probar contraseñas indefinidamente. Aplicar rate limiting + lockout progresivo. |
| **Registro autoservicio sin verificación = cuentas no controladas** | Con registro abierto, cualquiera con acceso a la VPN puede crearse una cuenta. La verificación de email es la única barrera real — si se implementa mal (ej. permitir usar la app antes de verificar, o validación débil del token de verificación) el control queda vacío. |
| **Session fixation / hijacking** | Cookies de sesión deben ser `HttpOnly`, `Secure` (requiere TLS incluso en LAN) y `SameSite=Lax` o `Strict`. Regenerar el ID de sesión tras login exitoso. |
| **CSRF ahora con impacto real** | A diferencia de la versión sin auth, ahora un CSRF exitoso puede operar *con los privilegios de la víctima autenticada* (ej. borrar sus partituras, iniciar transcripciones a su nombre). El token CSRF debe estar atado a la sesión autenticada. |
| **Enumeración de usuarios vía registro/login** | Respuestas distintas según si el email ya existe permiten enumerar cuentas válidas — más relevante todavía con registro abierto que con invitación. |

**Componente nuevo: SMTP (verificación de email + reseteo de contraseña)**

| Amenaza | Detalle |
|---|---|
| **Credenciales SMTP expuestas** | Deben vivir en variables de entorno/secrets manager, nunca en el repo. Si se usa una cuenta de terceros (Gmail, etc.), usar app password dedicado, no la contraseña principal. |
| **Email header injection** | Si algún dato controlado por el usuario (nombre, etc.) se inserta sin sanitizar en el asunto/cuerpo del correo, se puede inyectar headers adicionales (CC/BCC oculto, spoofing de remitente adicional). |
| **Tokens de verificación/reseteo predecibles o de larga duración** | Deben ser aleatorios criptográficamente seguros (no incrementales, no basados en timestamp), de un solo uso, e invalidados tras expiración corta (30-60 min para reseteo). |
| **Email bombing / abuso del endpoint de reseteo** | Sin rate limiting específico en `/forgot-password`, alguien puede spammear de correos a un usuario objetivo repitiendo el request. Rate limit por IP y por email destino. |
| **Bypass de verificación de email** | Si la cuenta queda "medio activa" (puede loguearse pero no se valida que esté verificada en cada request protegido), un atacante que se registra con un email ajeno igual podría usar la cuenta sin haber verificado nada. Verificar el flag `email_verified` en cada acceso a funcionalidad protegida, no solo al momento del login. |

### 4.8 Autorización y aislamiento multiusuario (multi-tenancy) — el boundary más crítico de esta versión

| Amenaza | Detalle |
|---|---|
| **IDOR (Insecure Direct Object Reference)** | Si una partitura se accede vía `/score/<id>` y el backend no verifica que `id` pertenezca al `user_id` de la sesión activa, cualquier usuario autenticado puede leer (o peor, borrar/modificar) partituras de otro con solo cambiar el número en la URL. Este es probablemente el riesgo más probable de toda la app, porque es un error de implementación fácil de cometer y fácil de explotar sin herramientas especiales. |
| **Listados sin filtro por usuario** | Un endpoint tipo `/api/scores` debe filtrar siempre por `user_id` de la sesión — nunca devolver "todas las partituras" y filtrar en el frontend (el filtrado client-side no es control de acceso). |
| **Escalación horizontal vía nombres de archivo predecibles** | Si los archivos de partitura se guardan en disco con rutas predecibles (`/storage/scores/123.pdf`), y además se sirven como estáticos sin pasar por el control de autorización de Flask, un usuario puede acceder a archivos de otro simplemente adivinando/iterando IDs — aunque la capa de aplicación esté bien protegida. |
| **Falta de rol de admin definido** | Si el equipo SecureHex va a administrar usuarios (aprobar registros, resetear contraseñas), definir un rol admin explícito con sus propios controles, no reusar cuentas normales con permisos especiales hardcodeados. |
| **Cancelación de jobs sin verificación de ownership** | Ahora que se permite cancelar jobs en progreso, el endpoint de cancelación es otro punto donde aplica el mismo principio: un usuario no debe poder cancelar el job de otro solo por adivinar/iterar el ID del job. |

### 4.85 Límite de duración: advertencia de UX vs. control real de seguridad

**Decisión de producto:** advertencia blanda a los 15 min que el usuario debe confirmar para continuar con archivos más largos.

Esta confirmación es una medida de experiencia de usuario, **no un control de seguridad**: el propio usuario decide si acepta o no, así que no limita nada frente a un usuario que quiera abusar del sistema (o simplemente uno descuidado). El riesgo de DoS/degradación de servicio identificado en las secciones 4.35 y la matriz de riesgo **sigue existiendo igual** a menos que exista, además, un **tope duro server-side no evadible** (ej. 60 min) que rechace la solicitud sin excepción, independiente de lo que el usuario confirme en el diálogo. Confirmar esto explícitamente con el equipo de implementación — es un error común asumir que un `confirm()` de JavaScript es una medida de seguridad.

### 4.9 Frontend (OSMD) (TB5)

| Amenaza | Detalle |
|---|---|
| **XSS almacenado/reflejado** | El título del video de YouTube/IG/TikTok, nombre de artista, o metadata del archivo de audio, puede terminar renderizado en el HTML (ej. "ahora transcribiendo: *{titulo}*") sin escapar. Como esos títulos son contenido no confiable de internet, es un vector de XSS clásico dentro de tu propia app local — que además podría usarse para disparar el CSRF de 4.1 contra el mismo backend. |
| **CDN sin SRI** | Si OSMD u otras libs JS se cargan desde CDN sin Subresource Integrity, un CDN comprometido es supply-chain risk directo. |

---

## 5. Matriz de riesgo (Likelihood × Impact)

| Riesgo | Likelihood | Impact | Prioridad |
|---|---|---|---|
| CSRF/DNS rebinding → SSRF vía yt-dlp | Media-Alta | Alto | **Crítica** |
| Command injection (yt-dlp / MuseScore CLI) | Media (depende de implementación) | Crítico (RCE) | **Crítica** |
| RCE vía parser de audio (ffmpeg/libsndfile) | Media | Alto | **Alta** |
| XSS vía metadata externa (título video) | Alta | Medio | **Alta** |
| Path traversal en nombres de salida | Media | Medio | Media |
| DoS por archivo/video de larga duración | Alta | Bajo-Medio | Media |
| DoS agravado por costo de cómputo de Demucs (sin GPU) | Alta | Medio | **Alta** |
| Disco lleno por stems WAV sin comprimir (6x tamaño por job) | Media | Bajo-Medio | Media |
| **IDOR — acceso a partituras de otro usuario** | Media-Alta (error común de implementación) | Alto | **Crítica** |
| **Fuerza bruta / credential stuffing en login** | Media (dentro de VPN, pero no cero) | Medio-Alto | **Alta** |
| **Contraseñas mal almacenadas (hashing débil)** | Baja si se sigue la guía, Alta si se improvisa | Crítico | **Alta (preventiva)** |
| **CSRF con sesión autenticada** | Media | Alto (ahora actúa con privilegios de la víctima) | **Alta** |
| Enumeración de usuarios vía registro/login | Alta | Bajo | Media |
| Reseteo de contraseña débil (sin mail server confiable) | Media (si se implementa apurado) | Medio | Media |
| **Advertencia de duración evadible (control solo client-side)** | Alta si no hay tope duro server-side | Medio-Alto | **Alta** |
| **Múltiples jobs Demucs simultáneos saturan la máquina (sin GPU dedicada)** | Media-Alta con varios usuarios activos | Medio | **Alta** |
| Email header injection / abuso de SMTP | Baja | Medio | Media |
| Enumeración/abuso de `/forgot-password` (email bombing) | Media | Bajo-Medio | Media |
| Retención indefinida aumenta el valor del dato ante cualquier brecha (IDOR, DB comprometida) | N/A (es un multiplicador de impacto, no un riesgo en sí) | Aumenta el impacto de los riesgos de la sección 4.8 | Nota transversal |
| XXE en import futuro de MusicXML | Baja hoy (no existe la función) | Alto si se implementa mal | Media (preventiva) |
| Info disclosure vía temp files residuales | Baja | Bajo | Baja |
| Supply chain (yt-dlp/TensorFlow desactualizados) | Alta (con el tiempo) | Variable | Media-continua |

---

## 6. Mitigaciones recomendadas (mapeadas a cada riesgo)

1. **Bind estricto a 127.0.0.1**, nunca `0.0.0.0`, y sin exponer el puerto vía reverse proxy accidental.
2. **CSRF token** incluso en app local sin login (ej. token de sesión efímero generado al cargar la página, validado en cada POST). No asumir que "local = sin CSRF".
3. **Validación de `Origin`/`Referer`** en cada request POST — rechazar si no coincide con `http://127.0.0.1:PORT`.
4. **Allowlist estricto de dominios** para yt-dlp (youtube.com, youtu.be, instagram.com, tiktok.com y sus CDN conocidos) — validar el host de la URL *antes* de pasarla a yt-dlp, no confiar en que yt-dlp la rechace.
5. **Nunca `shell=True`** en subprocess — siempre lista de argumentos (`subprocess.run([...], shell=False)`), para yt-dlp, ffmpeg y MuseScore CLI por igual.
6. **Sanitizar/generar nombres de archivo internamente** (UUID), nunca derivarlos de metadata externa (título del video). Si querís mostrar el título bonito, que viva solo como texto de UI, nunca como parte de un path o comando.
7. **Límites de tamaño y duración** en upload y en descarga de yt-dlp (ej. rechazar >15 min o >100MB), con `timeout` en todos los subprocess.
8. **Pinear versiones** de yt-dlp, ffmpeg, TensorFlow/basic-pitch, music21, y tener un proceso simple de actualización periódica (yt-dlp en particular hay que actualizarlo seguido).
9. **Escapar todo output de metadata externa** antes de insertarlo en el DOM (usar `textContent`, nunca `innerHTML`, en el frontend).
10. **Deshabilitar resolución de entidades externas** en cualquier parser XML usado (aunque hoy no se importe XML externo, dejarlo bloqueado por defecto).
11. **Directorio temporal dedicado** con `tempfile.mkdtemp()` (no nombres predecibles), permisos restrictivos (0700), y limpieza garantizada con `try/finally` o context manager al terminar cada job.
12. **`DEBUG=False`** siempre, sin stack traces expuestos al navegador.
13. Considerar correr el paso de yt-dlp/ffmpeg con **límites de recursos del OS** (ulimit, o un timeout duro tipo `subprocess.run(..., timeout=N)`).
14. **Aplicar el mismo timeout/límite de recursos a Demucs**, ajustado a que es más pesado que ffmpeg/basic-pitch — considerar un límite de duración de entrada más estricto específicamente para el paso de separación (ej. 10 min en vez de 15) si la máquina no tiene GPU.
15. **Verificar la fuente de descarga de los pesos pre-entrenados de Demucs** (HTTPS, repo oficial) y cachearlos localmente después de la primera descarga para no depender de red en cada ejecución.
16. **Limpiar los stems intermedios** (WAV sin comprimir) tan pronto se generen los MIDI/MusicXML correspondientes — no dejarlos acumulándose en el directorio temporal del job.
17. **Toda query/endpoint que devuelva o modifique una partitura debe verificar `WHERE user_id = session.user_id`** — nunca confiar en que el frontend no muestre el botón, ni en que el ID sea "difícil de adivinar". Esto es lo primero que hay que probar manualmente (o con Burp) apenas exista el feature de guardar partituras.
18. **Usar Argon2id (o bcrypt con costo ≥12)** para contraseñas — nunca hashing propio ni algoritmos rápidos (MD5/SHA1/SHA256 sin key stretching).
19. **Rate limiting en `/login` y `/register`** (ej. Flask-Limiter) con backoff progresivo tras intentos fallidos.
20. **Cookies de sesión con `HttpOnly`, `Secure`, `SameSite=Strict`** — `Secure` implica correr con TLS incluso dentro de la VPN (certificado autofirmado o de una CA interna es suficiente para este caso de uso).
21. **Regenerar el session ID tras login exitoso** (previene session fixation).
22. **CSRF token atado a la sesión autenticada**, validado en todo POST/PUT/DELETE que modifique estado.
23. **Registro por invitación o aprobación de admin**, dado que el universo de usuarios reales es acotado (familia + equipo SecureHex) — evita registro abierto sin control dentro de la VPN.
24. **Respuestas de registro/login uniformes** independiente de si el email/usuario existe o no, para minimizar enumeración.
25. **Servir archivos de partitura (PDF/MusicXML) siempre a través de un endpoint autenticado de Flask que valide ownership** — nunca como archivos estáticos servidos directo por rutas predecibles.
26. **Tope duro server-side de duración (ej. 60 min), no evadible por el usuario** — independiente de la advertencia de 15 min, que es solo UX.
27. **Limitar concurrencia de jobs Demucs** (ej. 1-2 simultáneos vía cola Redis/RQ) dado que no hay GPU dedicada — protege disponibilidad del servicio para todos los usuarios de la VPN.
28. **Verificar ownership también en el endpoint de cancelación de jobs**, mismo criterio que partituras.
29. **Flag `email_verified` chequeado en cada acceso a funcionalidad protegida**, no solo al momento del login — evita que una cuenta "a medio verificar" quede utilizable.
30. **Tokens de verificación de email y de reseteo de contraseña**: aleatorios criptográficamente seguros, de un solo uso, expiración corta para reseteo (30-60 min).
31. **Rate limiting específico en `/forgot-password`** (por IP y por email destino), además del rate limiting genérico de auth.
32. **Sanitizar cualquier dato insertado en emails** (nombre de usuario, etc.) para prevenir header injection.
33. **Dado que la retención de partituras es indefinida por defecto**, tratar la base de datos y el storage de archivos con el mismo nivel de cuidado que cualquier sistema que acumula datos con el tiempo — backups considerados, y controles de acceso (secciones 4.7/4.8) revisados con más rigor porque el impacto de una falla crece a medida que se acumulan más partituras.

---

## 7. Nota no-técnica

La descarga de contenido de YouTube/Instagram/TikTok vía yt-dlp para fines de transcripción personal cae en zona gris de ToS de esas plataformas (similar a lo que señalaban los artículos sobre Songscription). El hecho de descartar el audio/video original tras procesar reduce la exposición (no se acumula una biblioteca de contenido con copyright), pero la partitura derivada persistida sigue siendo, en rigor, una obra derivada — vale la pena tenerlo presente ya que ahora hay varios usuarios usando la herramienta, no solo vos. El carácter open source del proyecto no cambia esta nota, pero sí significa que terceros que clonen Clavis heredan la misma zona gris si usan la función de ingesta por link.

## 8. Open source: superficie adicional

Al ser Clavis un proyecto open source (repo público, cualquiera puede clonarlo y correrlo, y potencialmente contribuir), se suman consideraciones que no existían en un proyecto interno de SecureHex:

| Tema | Detalle |
|---|---|
| **Secrets en el repo** | El riesgo clásico de proyectos open source: alguien commitea por error una credencial SMTP, un `SECRET_KEY`, o una connection string de la DB. Un solo commit histórico con un secret expuesto sigue siendo recuperable del historial de git aunque se borre después. Mitigación: `.gitignore` correcto desde el primer commit, `.env.example` sin valores reales, y idealmente un pre-commit hook que escanee secrets (ej. `gitleaks`). |
| **Supply chain más visible = más escrutinio, pero también más superficie de typosquatting** | Al publicar `requirements.txt`/`package.json`, queda claro qué dependencias usa Clavis (yt-dlp, Demucs, basic-pitch, etc.) — esto es positivo para auditoría pero también facilita que alguien intente typosquatting de paquetes con nombres parecidos si algún colaborador instala mal una dependencia. Con la licencia GPL ya definida para Clavis, no hay tensión de compatibilidad con MuseScore CLI (también GPLv3) — de todas formas, listar las licencias de cada dependencia en el README/NOTICE sigue siendo buena práctica. |
| **PRs externos maliciosos** | Si el proyecto acepta contribuciones externas, un PR podría introducir código malicioso disfrazado de una feature legítima — especialmente peligroso en las zonas ya identificadas como sensibles (subprocess, auth, autorización). Requiere revisión humana cuidadosa, no solo tests automatizados, antes de mergear cambios en esas áreas. |
| **CI/CD con secrets** | Si se configura GitHub Actions, un PR de un fork externo no debería poder acceder a secrets de producción/deploy — GitHub tiene protecciones por defecto para esto, pero hay que verificar la configuración, no asumirla. |
| **Divulgación responsable de vulnerabilidades** | Con `SECURITY.md`, se le da a la comunidad un canal privado para reportar problemas de seguridad en vez de abrirlos como issue público (lo que sería un 0-day público para cualquiera que corra su propia instancia de Clavis). |
| **Documentar el alcance de seguridad asumido** | El threat model completo asume despliegue en LAN/VPN privada. Si un tercero clona el repo y lo expone directamente a internet sin ajustar nada, hereda automáticamente todos los riesgos que en este documento quedaron "fuera de alcance" por decisión de diseño (rate limiting más agresivo, TLS con CA válida en vez de autofirmado, protección anti-bot, etc.). Dejarlo explícito en el README evita que terceros asuman una postura de seguridad que Clavis no ofrece por defecto en ese escenario. |

---

## Historial del documento

Este threat model se ha actualizado incrementalmente junto con las decisiones de producto del proyecto: versión inicial single-user/efímero → adición de Demucs → cambio de alcance a multiusuario en LAN/VPN → resolución de decisiones abiertas (registro autoservicio, SMTP, retención indefinida, etc.) → adopción del nombre Clavis y confirmación de que es open source. Cada cambio de alcance material debe seguir generando una revisión de este documento, no solo del `CLAUDE.md`.
