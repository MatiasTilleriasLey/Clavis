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


def _run_mscore(mscore_bin, input_path, output_path):
    """MuseScore CLI headless: convierte input->output según extensiones. shell=False (§6.5)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    subprocess.run(
        [mscore_bin, "-o", output_path, input_path],
        check=True, timeout=MUSESCORE_TIMEOUT, capture_output=True, shell=False, env=env,
    )


def musicxml_to_pdf(xml_path, pdf_path, mscore_bin):
    _run_mscore(mscore_bin, xml_path, pdf_path)


def _consolidate_midi(midi_path, bpm):
    """Funde el MIDI del modelo en una sola pista de piano con el tempo estimado. MuseScore
    auto-separa las manos en un gran pentagrama al importar una única pista de piano."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(midi_path)
    notes = [n for inst in pm.instruments for n in inst.notes]
    out = pretty_midi.PrettyMIDI(initial_tempo=float(bpm) if bpm else 120.0)
    piano = pretty_midi.Instrument(program=0, name="Piano")
    piano.notes = notes
    out.instruments.append(piano)
    out.write(midi_path)


def _set_musicxml_metadata(xml_path, title, composer, arranger):
    """Inyecta título/autor/arreglo en un MusicXML (edición dirigida y escapada; NO se hace
    round-trip por music21 para no re-romper la notación limpia de MuseScore)."""
    import re
    from xml.sax.saxutils import escape
    txt = open(xml_path, encoding="utf-8").read()

    if title is not None:
        t = escape(title)
        if "<movement-title>" in txt:
            txt = re.sub(r"<movement-title>.*?</movement-title>",
                         f"<movement-title>{t}</movement-title>", txt, count=1, flags=re.S)
        else:
            txt = re.sub(r"(<score-partwise[^>]*>)",
                         rf"\1\n  <movement-title>{t}</movement-title>", txt, count=1)

    # composer/arranger como <creator> dentro de <identification>
    creators = ""
    if composer:
        creators += f'\n    <creator type="composer">{escape(composer)}</creator>'
    if arranger:
        creators += f'\n    <creator type="arranger">{escape(arranger)}</creator>'
    # limpiar creators previos que hayamos puesto, luego insertar
    txt = re.sub(r'\s*<creator type="(?:composer|arranger)">.*?</creator>', "", txt, flags=re.S)
    if creators and "<identification>" in txt:
        txt = txt.replace("<identification>", "<identification>" + creators, 1)

    open(xml_path, "w", encoding="utf-8").write(txt)


def apply_metadata(xml_path, pdf_path, title, composer, arranger, mscore_bin):
    """Edita la metadata del MusicXML guardado y regenera el PDF (si hay MuseScore)."""
    _set_musicxml_metadata(xml_path, title, composer, arranger)
    if mscore_bin and pdf_path:
        _run_mscore(mscore_bin, xml_path, pdf_path)


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


def transcribe(audio_path, work_dir, title="", mscore_bin=None):
    """Audio de piano -> (musicxml, pdf). Genera la notación con MuseScore importando el MIDI
    (auto-separa manos, cuantiza y detecta armadura/tempo mucho mejor que music21).
    Requiere MuseScore. El MIDI queda en work_dir/notes.mid (se persiste para descarga)."""
    wav = os.path.join(work_dir, "norm.wav")
    midi = os.path.join(work_dir, "notes.mid")
    xml = os.path.join(work_dir, "score.musicxml")
    pdf = os.path.join(work_dir, "score.pdf")

    normalize_audio(audio_path, wav)
    audio_to_midi_piano(wav, midi)
    bpm = estimate_tempo(wav)
    _consolidate_midi(midi, bpm)

    if not mscore_bin:
        raise RuntimeError("MuseScore es requerido para generar la partitura (configurar MSCORE_BIN)")
    _run_mscore(mscore_bin, midi, xml)          # MIDI -> MusicXML (gran pentagrama)
    _set_musicxml_metadata(xml, title, "", "")  # título de la canción
    _run_mscore(mscore_bin, xml, pdf)           # MusicXML (con título) -> PDF
    return xml, pdf
