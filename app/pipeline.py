"""Pipeline audio -> MusicXML. Función pura reutilizable: la usa el request sync (paso 6-7)
y después el worker de la cola (paso 10). Todo ocurre dentro de un work_dir efímero.

Imports de basic-pitch/music21 son perezosos: cargar TensorFlow es lento y no debe pesar
en el arranque del web app ni si el proceso nunca transcribe."""
import os
import subprocess

FFMPEG_TIMEOUT = 120     # s; el límite de duración de contenido es aparte (paso 12)
MUSESCORE_TIMEOUT = 180  # más generoso: MuseScore arranca lento en headless


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


def transcribe(audio_path, work_dir):
    """Audio validado -> path del MusicXML generado dentro de work_dir."""
    wav = os.path.join(work_dir, "norm.wav")
    midi = os.path.join(work_dir, "notes.mid")
    xml = os.path.join(work_dir, "score.musicxml")
    normalize_audio(audio_path, wav)
    audio_to_midi(wav, midi)
    midi_to_musicxml(midi, xml)
    return xml
