"""Pipeline audio de piano -> MusicXML (gran pentagrama). Lo usa el worker de la cola.
Todo ocurre dentro de un work_dir efímero.

Los imports de los modelos (piano_transcription_inference/music21/librosa) son perezosos:
cargar el checkpoint es lento y no debe pesar en el arranque del web app."""
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


def audio_to_midi_piano(wav, midi_path):
    """Transcribe piano con el modelo de ByteDance (SOTA en piano solo), muy superior a
    basic-pitch para este caso. Corre en CPU (más lento, aceptable — sin GPU)."""
    import librosa
    from piano_transcription_inference import PianoTranscription, sample_rate
    audio, _ = librosa.load(wav, sr=sample_rate, mono=True)  # el modelo espera 16 kHz
    PianoTranscription(device="cpu").transcribe(audio, midi_path)


def estimate_tempo(wav):
    """Estima el tempo (BPM) del audio con librosa. None si no se puede."""
    try:
        import librosa
        y, sr = librosa.load(wav, sr=None, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
        return round(bpm) if bpm > 0 else None
    except Exception:
        return None


def _m21_instrument(name):
    from music21 import instrument as I
    return {
        "piano": I.Piano, "guitarra": I.AcousticGuitar, "bajo": I.AcousticBass,
        "voz": I.Vocalist, "bateria": I.Percussion, "otros": I.Piano, "mezcla": I.Piano,
    }.get(name, I.Piano)()


def _set_metadata(score, title, composer, arranger):
    from music21 import metadata
    score.insert(0, metadata.Metadata())
    score.metadata.title = title or "Transcripción"
    if composer:
        score.metadata.composer = composer
    if arranger:
        try:
            score.metadata.add("arranger", arranger)
        except Exception:
            pass


def _grand_staff(flat, detected_key):
    """Parte las notas en dos pentagramas (clave de sol / clave de fa) por el do central."""
    from music21 import clef, instrument, layout, stream
    right, left = stream.Part(), stream.Part()
    right.insert(0, instrument.Piano())
    left.insert(0, instrument.Piano())
    right.insert(0, clef.TrebleClef())
    left.insert(0, clef.BassClef())
    if detected_key is not None:
        right.insert(0, detected_key)
        left.insert(0, detected_key)
    for n in flat.notes:
        low = n.pitch.midi if n.isNote else min(p.midi for p in n.pitches)
        (right if low >= 60 else left).insert(n.offset, n)  # 60 = do central
    grand = stream.Score()
    grand.insert(0, right)
    grand.insert(0, left)
    grand.insert(0, layout.StaffGroup([right, left], symbol="brace", name="Piano"))
    return grand


def midi_to_musicxml(midi_path, xml_path, instrument="mezcla", title="", composer="",
                     arranger="", tempo=None):
    from music21 import converter
    from music21 import tempo as m21tempo
    score = converter.parse(midi_path)
    try:
        score = score.quantize()  # limpia duraciones; best-effort
    except Exception:
        pass

    # Detectar tonalidad para aplicar la armadura correspondiente (Krumhansl-Schmuckler).
    detected_key = None
    try:
        detected_key = score.analyze("key")
    except Exception:
        pass

    if instrument == "piano":
        score = _grand_staff(score.flatten(), detected_key)
        target = score.parts[0]  # aún sin compases: el offset 0 entra en makeMeasures
    else:
        top = score.parts[0] if score.parts else score
        top.insert(0, _m21_instrument(instrument))
        top.partName = instrument.capitalize()
        # El Part ya tiene compases: hay que insertar clave/armadura/tempo DENTRO del 1er compás,
        # o music21 los descarta al exportar.
        target = top.getElementsByClass("Measure").first() or top
        if detected_key is not None:
            target.insert(0, detected_key)

    if tempo:
        for mm in list(score.recurse().getElementsByClass(m21tempo.MetronomeMark)):
            try:
                mm.activeSite.remove(mm)  # quita el tempo default del MIDI (evita duplicado)
            except Exception:
                pass
        target.insert(0, m21tempo.MetronomeMark(number=tempo))  # marca ♩ = N

    _set_metadata(score, title, composer, arranger)
    score.write("musicxml", fp=xml_path)


def apply_metadata(xml_path, title, composer, arranger):
    """Reescribe solo la metadata (título/autor/arreglo) de un MusicXML ya generado."""
    from music21 import converter
    score = converter.parse(xml_path)
    _set_metadata(score, title, composer, arranger)
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


def transcribe(audio_path, work_dir, title=""):
    """Audio validado -> path del MusicXML generado dentro de work_dir.
    El MIDI intermedio queda en work_dir/notes.mid (se persiste para descarga)."""
    wav = os.path.join(work_dir, "norm.wav")
    midi = os.path.join(work_dir, "notes.mid")
    xml = os.path.join(work_dir, "score.musicxml")
    normalize_audio(audio_path, wav)
    audio_to_midi_piano(wav, midi)
    bpm = estimate_tempo(wav)
    midi_to_musicxml(midi, xml, instrument="piano", title=title, tempo=bpm)  # siempre piano
    return xml
