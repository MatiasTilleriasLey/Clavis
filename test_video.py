"""Checks de la transcripción visual de piano (VPT). Los magic bytes y la fusión audio/video no
necesitan nada instalado; la calibración y la detección de eventos necesitan opencv-python-headless
(scripts/install_ml.sh) y se saltean si falta.  Corre: .venv/bin/python test_video.py"""
import os
import tempfile

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.video_transcription import (WHITE_PC, _press_runs,  # noqa: E402
                                     detect_video_kind, merge_notes)

try:
    import cv2
    import numpy as np

    from app.video_transcription import _calibrate, analyze
    HAVE_CV = True
except ImportError:
    HAVE_CV = False

# --- teclado sintético: la inversa exacta del detector (blancas parejas, negra sobre la junta
# de la blanca a su izquierda), que es lo que _calibrate tiene que reconstruir.
KB_X, KB_Y, KEY_W, KB_H = 20, 40, 24, 200
WHITES = [p for p in range(21, 109) if p % 12 in WHITE_PC]  # 52 blancas: A0..C8


def _key_box(pitch):
    """(x0, x1, y0, y1) de la tecla en la imagen sintética. En las blancas devuelve solo el
    frente (lo que se hunde y se ve); en las negras, el cuerpo entero."""
    if pitch % 12 in WHITE_PC:
        x = KB_X + WHITES.index(pitch) * KEY_W
        return x + 2, x + KEY_W - 2, KB_Y + int(KB_H * 0.80), KB_Y + KB_H
    cx = KB_X + (WHITES.index(pitch - 1) + 1) * KEY_W
    return cx - 7, cx + 7, KB_Y, KB_Y + int(KB_H * 0.6)


def _keyboard(pressed=()):
    """Frame de un teclado de 88 teclas. `pressed`: notas hundidas — la blanca se oscurece,
    la negra se aclara (los dos signos que el detector distingue por tipo de tecla)."""
    img = np.full((300, KB_X * 2 + len(WHITES) * KEY_W, 3), 60, np.uint8)
    img[KB_Y:KB_Y + KB_H, KB_X:KB_X + len(WHITES) * KEY_W] = 245
    for i in range(1, len(WHITES)):
        img[KB_Y:KB_Y + KB_H, KB_X + i * KEY_W] = 40      # junta entre blancas
    for p in range(21, 109):
        if p % 12 not in WHITE_PC:
            x0, x1, y0, y1 = _key_box(p)
            img[y0:y1, x0:x1] = 25                        # tecla negra
    for p in pressed:
        x0, x1, y0, y1 = _key_box(p)
        img[y0:y1, x0:x1] = 200 if p % 12 in WHITE_PC else 70
    return img


def test_magic_bytes():
    assert detect_video_kind(b"\x00\x00\x00\x20ftypisom\x00\x00") == "mp4"
    assert detect_video_kind(b"\x00\x00\x00\x14ftypqt  \x00\x00") == "mp4"   # mov
    assert detect_video_kind(b"\x1a\x45\xdf\xa3\x01\x00\x00\x00\x00\x00\x00\x1f") == "webm"
    assert detect_video_kind(b"RIFF\x24\x08\x00\x00WAVEfmt ") is None        # audio, no video
    assert detect_video_kind(b"%PDF-1.4\x00\x00\x00\x00") is None            # PDF disfrazado
    assert detect_video_kind(b"ftyp") is None                                # corto
    assert detect_video_kind(None) is None
    print("OK: magic bytes de video (7 aserciones)")


def test_press_runs():
    # tramo limpio
    assert _press_runs([0, 0, 1, 1, 1, 0, 0]) == [(2, 5)]
    # parpadeo de un frame en el medio: es la misma nota, no dos
    assert _press_runs([1, 1, 1, 0, 1, 1, 1]) == [(0, 7)]
    # tramo de un frame: ruido del umbral, no una nota
    assert _press_runs([0, 1, 0, 0]) == []
    # dos notas separadas de verdad siguen siendo dos
    assert _press_runs([1, 1, 0, 0, 0, 0, 1, 1]) == [(0, 2), (6, 8)]
    assert _press_runs([]) == []
    print("OK: extracción de tramos presionados (5 aserciones)")


def test_merge():
    audio = [
        # el pedal estiró esta nota hasta 3.0; el video vio la tecla volver a 1.5
        {"pitch": 60, "start": 1.00, "end": 3.00, "velocity": 90},
        # el evento visual cae fuera de la ventana: no es la misma nota, gana el audio
        {"pitch": 62, "start": 5.00, "end": 5.50, "velocity": 80},
        # sin evento visual (mano tapando la tecla): gana el audio, intacta
        {"pitch": 64, "start": 7.00, "end": 7.90, "velocity": 70},
    ]
    events = [
        {"pitch": 60, "start": 1.01, "end": 1.50},
        {"pitch": 62, "start": 5.30, "end": 5.60},   # +300 ms: fuera de tolerancia
        {"pitch": 67, "start": 9.00, "end": 9.40},   # nota que el audio no vio: se descarta
    ]
    notes, fixed = merge_notes(audio, events)
    assert fixed == 1, fixed
    assert len(notes) == 3, "el video no agrega ni saca notas, solo corrige tiempos"
    by = {n["pitch"]: n for n in notes}
    assert by[60]["source"] == "video" and by[60]["end"] == 1.50, by[60]
    assert by[60]["start"] == 1.01 and by[60]["velocity"] == 90, "pitch/velocity siguen del audio"
    assert by[62]["source"] == "audio" and by[62]["end"] == 5.50, by[62]
    assert by[64]["source"] == "audio" and by[64]["end"] == 7.90, by[64]
    assert 67 not in by

    # nota repetida: un evento visual explica una sola nota de audio, no las dos
    audio2 = [{"pitch": 60, "start": 1.00, "end": 2.00, "velocity": 90},
              {"pitch": 60, "start": 1.05, "end": 2.00, "velocity": 90}]
    notes2, fixed2 = merge_notes(audio2, [{"pitch": 60, "start": 1.04, "end": 1.20}])
    assert fixed2 == 1, fixed2
    assert sorted(n["source"] for n in notes2) == ["audio", "video"], notes2

    # nota repetida con sus dos eventos: se emparejan en orden, sin cruzarse
    notes5, fixed5 = merge_notes(audio2, [{"pitch": 60, "start": 1.01, "end": 1.04},
                                          {"pitch": 60, "start": 1.06, "end": 1.30}])
    assert fixed5 == 2, fixed5
    assert [(n["start"], n["end"]) for n in notes5] == [(1.01, 1.04), (1.06, 1.30)], notes5

    # el video nunca deja una nota en duración cero
    notes3, _ = merge_notes([{"pitch": 60, "start": 1.0, "end": 2.0, "velocity": 90}],
                            [{"pitch": 60, "start": 1.0, "end": 1.0}])
    assert notes3[0]["end"] > notes3[0]["start"], notes3

    # sin eventos (calibración fallida) el audio pasa entero: el fallback no pierde nada
    notes4, fixed4 = merge_notes(audio, [])
    assert fixed4 == 0 and len(notes4) == 3
    assert all(n["source"] == "audio" for n in notes4)
    print("OK: fusión audio/video + fallback por oclusión y fuera de tolerancia (14 aserciones)")


def test_calibrate():
    _, keys = _calibrate(_keyboard(), cv2, np)
    pitches = sorted(k[0] for k in keys)
    assert pitches == list(range(21, 109)), f"mapeo incompleto: {len(pitches)} teclas"
    blacks = {k[0] for k in keys if k[3]}
    assert blacks == {p for p in range(21, 109) if p % 12 not in WHITE_PC}, "blancas/negras cruzadas"
    print("OK: calibración -> 88 teclas A0..C8 con blancas y negras bien separadas")


def _write_video(path, fps=30.0, n=90, press=(30, 50), pitches=(60, 66)):
    """Video sintético: teclado en reposo salvo `pitches` hundidas en los frames [press)."""
    frames = {False: _keyboard(), True: _keyboard(pitches)}
    h, w = frames[False].shape[:2]
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened(), "no se pudo abrir el VideoWriter (falta el codec mp4v)"
    for i in range(n):
        vw.write(frames[press[0] <= i < press[1]])
    vw.release()


def test_analyze():
    work = tempfile.mkdtemp(prefix="clavis_test_")
    path = os.path.join(work, "kb.mp4")
    _write_video(path)
    events = analyze(path)
    got = {e["pitch"] for e in events}
    assert got == {60, 66}, f"esperaba C4 (blanca) y F#4 (negra), detecté {sorted(got)}"
    for e in events:
        # frames 30..50 a 30 fps => 1.000 s .. 1.667 s
        assert abs(e["start"] - 1.0) < 0.06, e
        assert abs(e["end"] - 1.667) < 0.06, e
    print("OK: detección de eventos en video (onset/offset de blanca y negra, ±60 ms)")


def run():
    test_magic_bytes()
    test_press_runs()
    test_merge()
    if not HAVE_CV:
        print("SKIP: OpenCV no instalado (correr scripts/install_ml.sh)")
        return
    test_calibrate()
    test_analyze()


if __name__ == "__main__":
    run()
