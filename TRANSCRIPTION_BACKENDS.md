# Diseño — Motores de transcripción de piano (incl. opcionales de pago)

> **Estado:** propuesta de diseño. **No implementado.** Documenta cómo se agregarían motores
> de transcripción de piano alternativos (incluidos comerciales) sin romper el diseño de Clavis.
> Autor de la idea: Matías Tillerías Ley.

## 1. Objetivo

Hoy la transcripción de audio→MIDI está fija a un solo modelo (**ByteDance
`piano_transcription_inference`**, open source, local). Queremos poder **elegir el motor de
transcripción por trabajo**, sumando de forma **opcional** motores de pago que puedan dar mejor
calidad, **sin que la app deje de funcionar 100% gratis y local**.

**Invariante:** el motor **`local` (ByteDance) es siempre el default y no requiere nada**. Cualquier
motor de pago es aditivo: si no está configurado, no existe para el usuario.

## 2. La distinción que define todo: local vs. nube

| | **Local de pago** (ej. AnthemScore) | **Nube de pago** (ej. Klangio) |
|---|---|---|
| Dónde corre | Dentro de tu red | Servidores del proveedor |
| ¿El audio sale de la red? | **No** | **Sí** |
| Impacto en el threat model | Ninguno (como MuseScore, pero con licencia paga) | **Sale del alcance LAN** — hay que tratarlo como excepción explícita |
| Setup | Instalar binario + ruta en config | API key en config |

Esto importa porque el pilar de Clavis (`CLAUDE.md`, `THREAT_MODEL.md`) es:
*"todo corre en tu red, sin salir afuera"* + *"el audio nunca se persiste / no se loguea contenido"*.

- **Local de pago:** encaja limpio. No cambia la postura de seguridad.
- **Nube de pago:** contradice el pilar. Se puede hacer, pero **solo como opt-in explícito por
  trabajo**, con aviso, y documentándolo como excepción al alcance (ver §6).

## 3. Arquitectura propuesta

### 3.1 Capa de backends seleccionables

Hoy `pipeline.transcribe()` (en `app/pipeline.py:275`) llama directo a
`audio_to_midi_piano(wav, midi)` (línea 285). Ese es **el único punto de inserción**: la
transcripción es un paso `wav → midi`; todo lo demás del pipeline (MuseScore, clefs, metadata,
PDF) es idéntico para cualquier motor.

Se introduce un **registro de motores**, cada uno con la misma firma `(wav_path, midi_path) -> None`:

```python
# app/transcribers.py  (nuevo)

class Transcriber:
    key: str            # "local", "anthemscore", "klangio"
    label: str          # "ByteDance (local, gratis)"
    remote: bool        # True = manda el audio afuera (nube)
    def available(self, cfg) -> bool: ...     # ¿está configurado/instalado?
    def run(self, wav_path, midi_path, cfg) -> None: ...

TRANSCRIBERS = {
    "local":       LocalByteDance(),     # siempre available()=True, remote=False
    "anthemscore": AnthemScoreCLI(),     # available si hay ruta al binario; remote=False
    "klangio":     KlangioAPI(),         # available si hay API key; remote=True
}

def get(key, cfg):
    t = TRANSCRIBERS.get(key) or TRANSCRIBERS["local"]
    return t if t.available(cfg) else TRANSCRIBERS["local"]   # fallback al local
```

`transcribe()` pasa a recibir el motor elegido y reemplaza la llamada directa:

```python
def transcribe(audio_path, work_dir, title="", mscore_bin=None, engine="local"):
    ...
    normalize_audio(audio_path, wav)
    transcribers.get(engine, cfg).run(wav, midi)   # <- antes: audio_to_midi_piano(wav, midi)
    ...
```

El `local` es simplemente el actual `audio_to_midi_piano` movido a `LocalByteDance.run`.

### 3.2 Selección por trabajo

- En el dashboard, un `<select>` **"Motor de transcripción"** en las pestañas Audio/Link (no en
  MIDI — ahí no hay transcripción). Solo lista los motores con `available()==True`.
- El valor viaja en el POST de `/upload` y `/ingest`, se valida contra el registro (allowlist de
  keys), y se guarda en el `Job` (nueva columna `Job.engine`, default `"local"`).
- `jobs.transcribe_job` / `ingest_job` pasan `engine` a `transcribe()`.

### 3.3 Configuración y secrets (por el admin)

Reutiliza el patrón que **ya existe** para el SMTP (tabla `Setting` clave/valor, editable en
`/admin`). Nada de secrets en el repo (`.env`/DB, nunca commiteado):

- `anthemscore_bin` → ruta al binario (motor local de pago).
- `klangio_api_key` → API key (motor nube).
- Un motor sin su config → `available()==False` → **no aparece** en el `<select>`.

### 3.4 Fallback y errores

- Motor elegido no disponible al ejecutar → cae a `local` (log técnico sin contenido).
- Motor de pago falla (cuota agotada, red, HTTP 4xx/5xx, timeout) → el job se marca `failed` con
  un mensaje claro para el usuario (*"el motor X falló: cuota/credenciales/red"*), **sin** loguear
  el audio ni el contenido. Opcional: reintentar con `local` automáticamente (a decidir, §7).

## 4. Cambios por archivo (resumen)

| Archivo | Cambio |
|---|---|
| `app/transcribers.py` | **nuevo** — registro + clases `LocalByteDance`, `AnthemScoreCLI`, `KlangioAPI` |
| `app/pipeline.py` | `transcribe(..., engine="local")` usa el registro en vez de la llamada fija; mover el cuerpo de `audio_to_midi_piano` a `LocalByteDance` |
| `app/models.py` | `Job.engine` (String, default `"local"`); claves nuevas en `Setting` |
| `app/jobs.py` | `enqueue_*` y `*_job` propagan `engine` |
| `app/main/routes.py` | `/upload` y `/ingest` leen y validan `engine`; aviso obligatorio si `remote` |
| `app/templates/dashboard.html` | `<select>` de motor (solo los `available`) |
| `app/templates/admin_smtp.html` (o nuevo panel) | campos de config de cada motor |
| `migrations/` | Alembic: columna `Job.engine` |
| `README.md` / `THREAT_MODEL.md` | documentar los motores y la **excepción de alcance** si se usa nube |

## 5. Proveedores candidatos

| Motor | Tipo | Costo | Privacidad | Notas |
|---|---|---|---|---|
| **ByteDance** `piano_transcription_inference` | Local | Gratis (Apache-2.0) | Local | **El default actual.** SOTA en piano solo. |
| **AnthemScore** (Lunaverus) | Local | Licencia paga (~US$50–70) | Local | CLI, corre offline. **El más compatible con Clavis.** Verificar términos de licencia CLI. |
| **Klangio** (*Piano2Notes* / API) | Nube | Suscripción / por uso | **Sale a internet** | API REST. Requiere opt-in + aviso (§6). |
| **La Touche Musicale** | Nube/App | Suscripción | Sale a internet | Menos orientado a API. |
| Magenta *Onsets&Frames* / MT3, Transkun | Local | Gratis | Local | Alternativas open para comparar calidad (no son "de pago", pero encajan en la misma capa). |

> Antes de integrar cualquiera de pago: revisar sus **términos de licencia/uso** (que permitan uso
> programático/CLI o vía API), y que sean **compatibles con GPLv3** si se distribuye algo — para los
> de nube, la GPL no aplica al servicio remoto, pero sí hay que aclarar la dependencia externa.

## 6. Seguridad — motores de nube (solo si se integra uno)

Un motor `remote=True` **rompe el alcance LAN**, así que se trata como excepción explícita:

- [ ] **Opt-in por trabajo**, nunca por defecto. Aviso claro: *"esto envía tu audio a &lt;proveedor&gt;,
      fuera de tu red. ¿Continuar?"* (patrón similar al aviso de duración >15 min ya existente).
- [ ] **TLS** verificado hacia el proveedor; **timeout** duro en la llamada.
- [ ] El audio se **sigue borrando** tras procesar (nada nuevo se persiste).
- [ ] **No loguear** el contenido (audio, título, respuesta cruda) — solo eventos técnicos.
- [ ] API key en `Setting`/env, **nunca** en el repo ni en logs.
- [ ] Manejo de **cuota/rate limit/errores** del proveedor sin filtrar detalles sensibles al cliente.
- [ ] Validar/limitar tamaño y duración **antes** de subir (evita costos sorpresa).
- [ ] Documentar en `THREAT_MODEL.md` que este modo queda **fuera del alcance LAN** y hereda los
      riesgos de mandar datos a un tercero.

Un motor `remote=False` (local de pago) **no** dispara nada de lo anterior — solo es una dependencia
comercial más, como MuseScore.

## 7. Plan de implementación por fases (cuando se decida avanzar)

1. **Refactor sin cambio de comportamiento:** crear `app/transcribers.py`, mover ByteDance a
   `LocalByteDance`, `transcribe(engine="local")`. Todo sigue igual. (Tests verdes.)
2. **Selección por job:** columna `Job.engine` + migración, `<select>` en el dashboard (con solo
   `local` por ahora), propagación por la cola.
3. **Primer motor de pago local (AnthemScore):** clase `AnthemScoreCLI` (subprocess seguro:
   `shell=False`, timeout, nombres UUID), config `anthemscore_bin` en `/admin`.
4. **(Opcional) Motor de nube (Klangio):** clase `KlangioAPI` + todo el checklist de §6 + aviso +
   doc de excepción de alcance.
5. Tests: fallback al local, validación de la key del motor, y (para nube) que el opt-in es
   obligatorio.

## 8. Decisiones abiertas

- ¿La selección de motor es **por usuario** (preferencia guardada) o **por trabajo** (elige cada
  vez)? Propuesta: por trabajo, con el último elegido como default.
- ¿Fallback automático al `local` cuando un motor de pago falla, o dejar el job `failed` para que el
  usuario decida? Propuesta: `failed` con mensaje claro (evita costos/sorpresas silenciosas).
- ¿Qué proveedor concreto se integra primero? (define §3.3 y §5).
