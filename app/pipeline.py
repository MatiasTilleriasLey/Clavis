"""Pipeline audio -> MusicXML. Función pura reutilizable: la usa el request sync (paso 6-7)
y después el worker de la cola (paso 10). Todo ocurre dentro de un work_dir efímero.

Imports de basic-pitch/music21 son perezosos: cargar TensorFlow es lento y no debe pesar
en el arranque del web app ni si el proceso nunca transcribe."""
import os
import subprocess
import sys

FFMPEG_TIMEOUT = 120     # s; el límite de duración de contenido es aparte (paso 12)
MUSESCORE_TIMEOUT = 180  # más generoso: MuseScore arranca lento en headless
DEMUCS_TIMEOUT = 3600    # muy generoso: sin GPU, Demucs es lento (§6.14)
DEMUCS_MODEL = "htdemucs_6s"

# Nombre de instrumento en la UI (es) -> stem de Demucs. Sirve también de allowlist:
# solo estos valores llegan al subprocess (§6.6), lo que viene del form se filtra contra esto.
STEM_MAP = {
    "voz": "vocals", "bateria": "drums", "bajo": "bass",
    "guitarra": "guitar", "piano": "piano", "otros": "other",
}


def normalize_audio(src, dst):
    """ffmpeg -> WAV mono 22.05kHz. shell=False, timeout, sin video (§6.5, §6.7)."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "22050", dst],
        check=True, timeout=FFMPEG_TIMEOUT, capture_output=True, shell=False,
    )


def audio_to_midi(wav, midi_path):
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict
    _, midi_data, _ = predict(wav, ICASSP_2022_MODEL_PATH)
    midi_data.write(midi_path)


def midi_to_musicxml(midi_path, xml_path):
    from music21 import converter
    score = converter.parse(midi_path)
    try:
        score = score.quantize()  # limpia duraciones; best-effort
    except Exception:
        pass
    score.write("musicxml", fp=xml_path)


def musicxml_to_pdf(xml_path, pdf_path, mscore_bin):
    """MusicXML -> PDF con MuseScore CLI headless. shell=False, timeout, offscreen (§6.5)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    subprocess.run(
        [mscore_bin, "-o", pdf_path, xml_path],
        check=True, timeout=MUSESCORE_TIMEOUT, capture_output=True, shell=False, env=env,
    )


def separate_stems(audio_path, work_dir, stems):
    """Separa el audio en los stems pedidos (Demucs, CPU). Devuelve {nombre_ui: wav_path}.
    `stems` se filtra contra STEM_MAP: nunca pasa texto externo al subprocess (§6.6)."""
    wanted = {ui: STEM_MAP[ui] for ui in stems if ui in STEM_MAP}
    if not wanted:
        return {}
    out = os.path.join(work_dir, "stems")
    subprocess.run(
        [sys.executable, "-m", "demucs", "-n", DEMUCS_MODEL, "-d", "cpu", "-o", out, audio_path],
        check=True, timeout=DEMUCS_TIMEOUT, capture_output=True, shell=False,
    )
    track = os.path.splitext(os.path.basename(audio_path))[0]
    stem_dir = os.path.join(out, DEMUCS_MODEL, track)
    result = {}
    for ui, demucs_name in wanted.items():
        p = os.path.join(stem_dir, f"{demucs_name}.wav")
        if os.path.exists(p):
            result[ui] = p
    return result


def transcribe(audio_path, work_dir):
    """Audio validado -> path del MusicXML generado dentro de work_dir."""
    wav = os.path.join(work_dir, "norm.wav")
    midi = os.path.join(work_dir, "notes.mid")
    xml = os.path.join(work_dir, "score.musicxml")
    normalize_audio(audio_path, wav)
    audio_to_midi(wav, midi)
    midi_to_musicxml(midi, xml)
    return xml
