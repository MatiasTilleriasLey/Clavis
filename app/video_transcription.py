"""Transcripción visual de piano (VPT): video del teclado -> eventos nota-on/off.

Complementa el pipeline de audio, no lo reemplaza. piano_transcription_inference acierta el
pitch y la dinámica, pero no distingue "la tecla sigue abajo" de "el sonido sigue por el pedal
de sustain", así que infla las duraciones. El video sí ve cuándo la tecla vuelve físicamente
arriba. Fusión: pitch/velocity del audio, onset/offset del video cuando ambos coinciden.

CV clásica con OpenCV (sin GPU, mismo worker único que el resto). Módulo opcional y aislado:
si falta cv2 o la calibración falla, el llamador se queda con el MIDI de audio tal cual.

Supuestos (fuera de alcance de este feature): cámara fija, teclado completo y visible desde
arriba/enfrente, sin zoom ni cortes. Solo piano.
"""
import logging
import time
from collections import defaultdict
from itertools import groupby

log = logging.getLogger(__name__)

VIDEO_TIMEOUT = 1800     # s de pared analizando frames (el worker no tiene otro freno acá)
TARGET_FPS = 30.0        # muestreo efectivo: 33 ms << ventana de matching, no hace falta más
MATCH_TOLERANCE = 0.075  # s; ventana audio<->video para dar dos detecciones por la misma nota
MIN_PRESS_FRAMES = 2     # tramos más cortos = parpadeo del umbral, no una nota
MIN_NOTE = 0.03          # s; piso de duración, el video nunca deja una nota en cero

# Vista cenital normalizada del teclado. 1280 px / 52 blancas ~ 24 px por tecla.
WARP_W, WARP_H = 1280, 160
BLACK_ZONE = 0.55        # franja superior: ahí viven las negras
WHITE_ROWS = (int(WARP_H * 0.78), WARP_H)          # frente de las blancas (sin negras encima)
BLACK_ROWS = (int(WARP_H * 0.15), int(WARP_H * 0.45))  # cuerpo de las negras, lejos de los bordes

# Umbrales de cambio de intensidad (media 0-255) contra la imagen de referencia. Separados por
# tipo porque el signo es distinto: la blanca se oscurece al hundirse (sombra + dedo), la negra
# se aclara (se inclina y refleja más luz). Son la perilla de calibración: dependen de la luz
# de la sala y de la cámara, no hay un valor universal.
WHITE_THR = 8.0
BLACK_THR = 8.0

FIRST_WHITE_MIDI = 21    # A0: primera blanca de un piano de 88. Ver _first_white_pitch().
WHITE_PC = (0, 2, 4, 5, 7, 9, 11)   # C D E F G A B
HAS_SHARP = (1, 1, 0, 1, 1, 1, 0)   # C# D# – F# G# A# –  (patrón 2-3, único cada 7 blancas)


class VideoError(Exception):
    """El video no sirve para VPT (no abre, no se calibra, se pasó del timeout)."""


def detect_video_kind(head):
    """'mp4'|'webm' según los primeros bytes, o None. Magic bytes, no extensión (§subprocess)."""
    if head is None or len(head) < 12:
        return None
    if head[4:8] == b"ftyp":            # mp4 / mov / m4v
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":  # EBML: webm / mkv
        return "webm"
    return None


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError as e:
        raise VideoError("OpenCV no está instalado (scripts/install_ml.sh)") from e


# --------------------------------------------------------------------- calibración

def _order_quad(pts):
    """Ordena 4 esquinas como [sup-izq, sup-der, inf-der, inf-izq] para la homografía."""
    import numpy as np
    s, d = pts.sum(axis=1), (pts[:, 0] - pts[:, 1])
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)],
                     pts[np.argmax(s)], pts[np.argmin(d)]], dtype="float32")


def _keyboard_quad(bg, cv2, np):
    """Esquinas del teclado: es la mancha clara más grande del frame (las teclas blancas, unidas
    por un cierre morfológico que tapa las negras). Otsu en vez de un umbral fijo: la exposición
    de la cámara varía y un número mágico no sobrevive a otra sala."""
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise VideoError("no se encontró el teclado en el video")
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.04 * gray.size:
        raise VideoError("el teclado ocupa muy poco del frame")
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.02, 0.04, 0.06, 0.08):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return _order_quad(approx.reshape(4, 2).astype("float32"))
    # ponytail: sin cuadrilátero limpio, el rect rotado corrige giro y escala pero no
    # perspectiva. Alcanza para cámara casi frontal; si no, la fusión no matchea y manda el audio.
    return _order_quad(cv2.boxPoints(cv2.minAreaRect(hull)).astype("float32"))


def _runs(mask):
    """[(inicio, fin)] de los tramos True de una secuencia booleana."""
    out, i = [], 0
    for val, grp in groupby(mask):
        n = sum(1 for _ in grp)
        if val:
            out.append((i, i + n))
        i += n
    return out


def _white_keys(gray, np):
    """[(x0, x1)] de las blancas: en la franja de abajo solo hay blancas separadas por líneas
    oscuras, así que cada tramo de columnas claras es una tecla."""
    col = gray[WHITE_ROWS[0]:WHITE_ROWS[1]].mean(axis=0)
    runs = _runs(col > (col.max() + col.min()) / 2.0)
    if len(runs) < 8:
        raise VideoError("no se distinguen las teclas blancas")
    w = float(np.median([b - a for a, b in runs]))
    keys = [(a, b) for a, b in runs if 0.5 * w <= b - a <= 1.8 * w]  # descarta sombras y slivers
    if len(keys) < 8:
        raise VideoError("no se distinguen las teclas blancas")
    return keys


def _black_keys(gray, cv2, np):
    """[(x0, x1)] de las negras: manchas oscuras altas en la franja superior."""
    top = gray[:int(WARP_H * BLACK_ZONE)]
    mask = (top < (int(top.max()) + int(top.min())) / 2.0).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in cnts]
    tall = [(x, x + w) for x, y, w, h in boxes if h > 0.4 * top.shape[0] and w > 2]
    return sorted(tall)


def _first_white_pitch(whites, blacks, hint):
    """Ancla el teclado a notas MIDI absolutas.

    La clase de nota sale del patrón de negras: mirando si cada blanca tiene una negra pegada a
    su derecha se obtiene 1101110 rotado, y esa rotación es única cada 7 blancas (el hueco doble
    E-F / B-C no se repite). La octava no es deducible de la geometría, así que sale de `hint`:
    se elige la octava que deje la primera blanca lo más cerca posible del teclado esperado.

    `hint` es la perilla: 21 (A0) asume piano de 88. Con un teclado de 61/76 quedará una octava
    corrida; eso no ensucia la partitura, simplemente no matchea con el audio y manda el audio.
    """
    w = whites[0][1] - whites[0][0]
    centers = [(a + b) / 2.0 for a, b in blacks]
    pattern = [1 if any(abs(c - x1) < 0.4 * w for c in centers) else 0 for _, x1 in whites]
    phase = max(range(7), key=lambda p: sum(
        obs == HAS_SHARP[(p + i) % 7] for i, obs in enumerate(pattern)))
    pc = WHITE_PC[phase]
    return min((pc + 12 * o for o in range(10)), key=lambda p: abs(p - hint))


def _white_pitch(first, i):
    """Nota MIDI de la i-ésima blanca contando desde la blanca `first`."""
    k = WHITE_PC.index(first % 12)
    o, j = divmod(k + i, 7)
    return first - WHITE_PC[k] + 12 * o + WHITE_PC[j]


def _calibrate(bg, cv2, np, hint=FIRST_WHITE_MIDI):
    """Frame de referencia -> (homografía, [(pitch, x0, x1, es_negra)]).

    Se calcula una vez por video y se reusa en cada frame: enderezar el teclado es caro y la
    cámara no se mueve (supuesto del feature)."""
    dst = np.array([[0, 0], [WARP_W - 1, 0], [WARP_W - 1, WARP_H - 1], [0, WARP_H - 1]], "float32")
    h = cv2.getPerspectiveTransform(_keyboard_quad(bg, cv2, np), dst)
    gray = cv2.cvtColor(cv2.warpPerspective(bg, h, (WARP_W, WARP_H)), cv2.COLOR_BGR2GRAY)

    whites = _white_keys(gray, np)
    blacks = _black_keys(gray, cv2, np)
    first = _first_white_pitch(whites, blacks, hint)

    keys = [(_white_pitch(first, i), x0, x1, False) for i, (x0, x1) in enumerate(whites)]
    w = whites[0][1] - whites[0][0]
    for x0, x1 in blacks:
        cx = (x0 + x1) / 2.0
        i = min(range(len(whites)), key=lambda k: abs(whites[k][1] - cx))
        if abs(whites[i][1] - cx) > 0.5 * w:
            continue                      # no está sobre una junta: no es una tecla negra
        pitch = _white_pitch(first, i) + 1
        if pitch % 12 in WHITE_PC:
            continue                      # sería el "sostenido" de E o B: falso positivo
        keys.append((pitch, x0, x1, True))
    keys.sort()
    return h, keys


# --------------------------------------------------------------------- detección por frame

def _profiles(gray):
    """Media por columna de las dos franjas (blancas abajo, negras arriba). Se hace una vez por
    frame y cada tecla lee su rango de columnas: 2 medias en vez de 88 recortes por frame."""
    return (gray[WHITE_ROWS[0]:WHITE_ROWS[1]].mean(axis=0),
            gray[BLACK_ROWS[0]:BLACK_ROWS[1]].mean(axis=0))


def _pressed(keys, prof, bg_prof, white_thr, black_thr):
    """Estado presionada/no de cada tecla en un frame, restando contra la referencia."""
    out = []
    for pitch, x0, x1, black in keys:
        cur, ref = (prof[1], bg_prof[1]) if black else (prof[0], bg_prof[0])
        a, b = float(cur[x0:x1].mean()), float(ref[x0:x1].mean())
        # signo por tipo de tecla: la negra se aclara al hundirse, la blanca se oscurece
        out.append((a - b > black_thr) if black else (b - a > white_thr))
    return out


def _press_runs(mask, min_frames=MIN_PRESS_FRAMES):
    """Tramos presionados usables: une huecos de un parpadeo y descarta lo demasiado corto."""
    merged = []
    for a, b in _runs(mask):
        if merged and a - merged[-1][1] < min_frames:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_frames]


def _background(cap, cv2, np, n=25):
    """Imagen del teclado en reposo: mediana de n frames repartidos por todo el video. Las manos
    se mueven, así que la mediana las borra — más robusto que promediar los primeros frames,
    donde las manos pueden estar ya apoyadas."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    if total > n:
        for i in np.linspace(0, total - 1, n).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, f = cap.read()
            if ok:
                frames.append(f)
    if len(frames) < 3:  # sin frame count fiable (streams, algunos webm): los primeros n
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frames = []
        for _ in range(n):
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
    if not frames:
        # ponytail: no transcodificamos. El ffmpeg que trae OpenCV no decodifica AV1: para los
        # links ya bajamos H.264 (ingest.VIDEO_FORMAT_SORT), y un upload en AV1 cae al audio solo.
        # Si aparece en la práctica: pasarlo a H.264 con el ffmpeg del sistema antes de abrirlo.
        raise VideoError("el video no tiene frames legibles (¿códec no soportado, ej. AV1?)")
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def analyze(video_path, hint=FIRST_WHITE_MIDI, white_thr=WHITE_THR, black_thr=BLACK_THR,
            timeout=VIDEO_TIMEOUT):
    """Video del teclado -> [{pitch, start, end}] en segundos. Lanza VideoError si no se calibra."""
    cv2 = _cv2()
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise VideoError("no se pudo abrir el video")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if not 1.0 <= fps <= 240.0:
            fps = 30.0  # el fps real sale del archivo; 30 es solo el último recurso
        step = max(1, round(fps / TARGET_FPS))  # 60fps -> analizamos 1 de cada 2

        bg = _background(cap, cv2, np)
        h, keys = _calibrate(bg, cv2, np, hint)
        bg_prof = _profiles(cv2.cvtColor(cv2.warpPerspective(bg, h, (WARP_W, WARP_H)),
                                         cv2.COLOR_BGR2GRAY))

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        series = [[] for _ in keys]
        t0, i = time.monotonic(), 0
        while cap.grab():                  # grab sin retrieve: no decodifica los frames salteados
            if i % step == 0:
                if time.monotonic() - t0 > timeout:
                    raise VideoError("timeout analizando el video")
                ok, frame = cap.retrieve()
                if not ok:
                    break
                gray = cv2.cvtColor(cv2.warpPerspective(frame, h, (WARP_W, WARP_H)),
                                    cv2.COLOR_BGR2GRAY)
                for k, on in enumerate(_pressed(keys, _profiles(gray), bg_prof,
                                                white_thr, black_thr)):
                    series[k].append(on)
            i += 1
    finally:
        cap.release()

    dt = step / fps
    events = [{"pitch": keys[k][0], "start": round(a * dt, 4), "end": round(b * dt, 4)}
              for k, mask in enumerate(series) for a, b in _press_runs(mask)]
    events.sort(key=lambda e: (e["start"], e["pitch"]))
    return events


# --------------------------------------------------------------------- fusión con el audio

def merge_notes(audio_notes, events, tol=MATCH_TOLERANCE):
    """Fusiona el MIDI de audio con los eventos vistos en el video -> (notas, corregidas).

    Reparto de autoridad: el audio manda en pitch y velocity siempre, y es el respaldo cuando
    la mano tapa la tecla (oclusión) o el umbral visual falla — sin evento visual dentro de la
    ventana, la nota de audio queda intacta. El video manda en onset/offset cuando ambos
    coinciden en pitch: es lo único que sabe cuándo se soltó la tecla de verdad, en vez de
    cuándo dejó de sonar por el pedal.

    Cada nota lleva `source` ('audio'|'video') para saber quién ganó al depurar; el MIDI no
    tiene dónde guardarlo, así que no sobrevive a la escritura.
    """
    by_pitch = defaultdict(list)
    for e in events:
        by_pitch[e["pitch"]].append(e)

    used, out, fixed = set(), [], 0
    for n in sorted(audio_notes, key=lambda n: n["start"]):
        cands = [(abs(e["start"] - n["start"]), i, e)
                 for i, e in enumerate(by_pitch.get(n["pitch"], ()))
                 if (n["pitch"], i) not in used and abs(e["start"] - n["start"]) <= tol]
        if not cands:
            out.append(dict(n, source="audio"))
            continue
        _, i, e = min(cands, key=lambda c: c[0])
        # Un evento visual explica una sola nota de audio, y las notas se recorren en orden:
        # ante una nota repetida, la primera se lleva el primer evento que la alcanza en vez de
        # cruzarse con el evento de la segunda.
        used.add((n["pitch"], i))
        out.append(dict(n, start=e["start"], end=max(e["end"], e["start"] + MIN_NOTE),
                        source="video"))
        fixed += 1
    out.sort(key=lambda n: (n["start"], n["pitch"]))
    return out, fixed


def correct_midi(midi_path, video_path, **kw):
    """Corrige in-place el onset/offset del MIDI de audio con lo que se ve en el video.
    Devuelve (notas_corregidas, notas_totales). Deja el MIDI intacto si el video no aporta.

    El pedal (CC64, detectado por audio) se preserva tal cual: es justamente lo que explica que
    el sonido siga después de que la tecla ya volvió arriba."""
    from .pipeline import midi_notes, notes_to_midi
    data = midi_notes(midi_path)
    events = analyze(video_path, **kw)
    if not events or not data["notes"]:
        return 0, len(data["notes"])
    notes, fixed = merge_notes(data["notes"], events)
    if fixed:
        notes_to_midi(notes, data["tempo"], midi_path, data["pedals"])
    log.info("vpt: %d/%d notas corregidas por video", fixed, len(notes))  # sin contenido (§logging)
    return fixed, len(notes)
