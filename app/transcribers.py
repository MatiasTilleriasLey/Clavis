"""Motores de transcripción de piano (WAV -> MIDI).

El motor `local` (ByteDance) es el default y **siempre** está disponible (viene con la app).
Los demás son opcionales y solo aparecen si están instalados. Sirve para hacer A/B de calidad
sobre el mismo audio sin cambiar nada del resto del pipeline (todo lo demás —MuseScore, claves,
metadata, PDF— es idéntico salga de donde salga el MIDI). Ver TRANSCRIPTION_BACKENDS.md.
"""
import os
import shutil
import subprocess
import sys

TRANSCRIBE_TIMEOUT = 1500  # tope duro por si un motor se cuelga (refuerza JOB_TIMEOUT)


def _transkun_bin():
    """Ubica el script `transkun`. Está junto al intérprete (.venv/bin/transkun), y el PATH del
    worker puede no incluir esa carpeta, así que lo resolvemos relativo a sys.executable primero."""
    cand = os.path.join(os.path.dirname(sys.executable), "transkun")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return shutil.which("transkun")


def _bytedance(wav_path, midi_path):
    """ByteDance `piano_transcription_inference` (SOTA en piano solo, Apache-2.0). CPU, local."""
    import librosa
    from piano_transcription_inference import PianoTranscription, sample_rate
    audio, _ = librosa.load(wav_path, sr=sample_rate, mono=True)  # el modelo espera 16 kHz
    PianoTranscription(device="cpu").transcribe(audio, midi_path)


def _transkun(wav_path, midi_path):
    """Transkun (open source): CLI `transkun <in> <out.mid>`. Subprocess seguro (shell=False,
    lista de argumentos, timeout). Baja su checkpoint la primera vez y lo cachea."""
    exe = _transkun_bin()
    if not exe:
        raise RuntimeError("transkun no está instalado")
    subprocess.run([exe, wav_path, midi_path], check=True, timeout=TRANSCRIBE_TIMEOUT,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# key -> metadata + implementación. remote=True marcaría "el audio sale de la red" (ninguno acá).
ENGINES = {
    "local":    {"label": "ByteDance (local)", "remote": False, "run": _bytedance,
                 "available": lambda: True},
    "transkun": {"label": "Transkun (local)",  "remote": False, "run": _transkun,
                 "available": lambda: _transkun_bin() is not None},
}
DEFAULT = "local"


def is_valid(engine):
    return engine in ENGINES


def label_for(engine):
    return (ENGINES.get(engine) or ENGINES[DEFAULT])["label"]


def available_engines():
    """Motores instalados/configurables, para poblar el <select> del dashboard."""
    return [{"key": k, "label": v["label"], "remote": v["remote"]}
            for k, v in ENGINES.items() if v["available"]()]


def run(engine, wav_path, midi_path):
    """Ejecuta el motor elegido. Si no es válido o no está disponible, cae al local."""
    e = ENGINES.get(engine)
    if e is None or not e["available"]():
        e = ENGINES[DEFAULT]
    e["run"](wav_path, midi_path)
