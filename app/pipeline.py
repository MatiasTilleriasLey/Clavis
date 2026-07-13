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


def _consolidate_midi(midi_path, bpm=None):
    """Funde el MIDI en una sola pista de piano. MuseScore auto-separa las manos en un gran
    pentagrama al importar una única pista de piano. Si bpm es None, usa el tempo del MIDI."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(midi_path)
    if bpm is None:
        try:
            _, tempi = pm.get_tempo_changes()
            bpm = float(tempi[0]) if len(tempi) else 120.0
        except Exception:
            bpm = 120.0
    notes = [n for inst in pm.instruments for n in inst.notes]
    out = pretty_midi.PrettyMIDI(initial_tempo=float(bpm) or 120.0)
    piano = pretty_midi.Instrument(program=0, name="Piano")
    piano.notes = notes
    for inst in pm.instruments:  # preservar pedal (CC64) y demás control changes
        piano.control_changes.extend(inst.control_changes)
    out.instruments.append(piano)
    out.write(midi_path)


def midi_notes(midi_path):
    """Lee el MIDI y devuelve {tempo, notes:[{pitch,start,end,velocity}]} para el editor."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(midi_path)
    tempo = 120.0
    try:
        _, tempi = pm.get_tempo_changes()
        tempo = float(tempi[0]) if len(tempi) else 120.0
    except Exception:
        pass
    notes = [{"pitch": n.pitch, "start": round(n.start, 4), "end": round(n.end, 4),
              "velocity": n.velocity}
             for inst in pm.instruments for n in inst.notes]
    notes.sort(key=lambda n: (n["start"], n["pitch"]))

    # pedal de sustain (CC64): construir rangos [start,end] a partir de los eventos on/off
    cc = sorted((c for inst in pm.instruments for c in inst.control_changes if c.number == 64),
                key=lambda c: c.time)
    end_of_song = max((n["end"] for n in notes), default=0.0)
    pedals, down = [], None
    for c in cc:
        if c.value >= 64 and down is None:
            down = round(float(c.time), 4)
        elif c.value < 64 and down is not None:
            pedals.append({"start": down, "end": round(float(c.time), 4)})
            down = None
    if down is not None:
        pedals.append({"start": down, "end": round(float(end_of_song), 4)})

    return {"tempo": tempo, "notes": notes, "pedals": pedals}


def notes_to_midi(notes, tempo, midi_path, pedals=None):
    """Escribe una pista de piano desde la lista de notas editada en el editor.
    pedals: lista de rangos {start,end} del pedal de sustain (se escriben como CC64 on/off)."""
    import pretty_midi
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo) or 120.0)
    ins = pretty_midi.Instrument(program=0, name="Piano")
    for n in notes:
        ins.notes.append(pretty_midi.Note(
            velocity=int(n["velocity"]), pitch=int(n["pitch"]),
            start=float(n["start"]), end=float(n["end"])))
    for p in pedals or []:
        ins.control_changes.append(pretty_midi.ControlChange(number=64, value=127, time=float(p["start"])))
        ins.control_changes.append(pretty_midi.ControlChange(number=64, value=0, time=float(p["end"])))
    pm.instruments.append(ins)
    pm.write(midi_path)


def midi_to_score(src_midi, work_dir, title="", mscore_bin=None):
    """MIDI subido por el usuario -> (musicxml, pdf). Igual que transcribe() pero sin el paso
    audio->MIDI: consolida a piano y MuseScore genera la notación. Deja notes.mid en work_dir."""
    import shutil
    midi = os.path.join(work_dir, "notes.mid")
    xml = os.path.join(work_dir, "score.musicxml")
    pdf = os.path.join(work_dir, "score.pdf")
    shutil.copy(src_midi, midi)
    _consolidate_midi(midi)  # tempo del propio MIDI
    if not mscore_bin:
        raise RuntimeError("MuseScore es requerido para generar la partitura (configurar MSCORE_BIN)")
    _run_mscore(mscore_bin, midi, xml)
    _lock_clefs(xml)
    _set_musicxml_metadata(xml, title, "", "")
    _run_mscore(mscore_bin, xml, pdf)
    return xml, pdf


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


def _lock_clefs(xml_path):
    """Fija clave de sol arriba y clave de fa abajo en el gran pentagrama, quitando los
    cambios de clave automáticos que mete MuseScore (confunden más de lo que ayudan)."""
    import re
    canon = {"1": '<clef number="1"><sign>G</sign><line>2</line></clef>',
             "2": '<clef number="2"><sign>F</sign><line>4</line></clef>'}
    seen = set()

    def repl(m):
        n = m.group(1)
        if n in seen:
            return ""  # elimina cambios de clave posteriores
        seen.add(n)
        return canon.get(n, m.group(0))  # fuerza la clave canónica en la primera aparición

    txt = open(xml_path, encoding="utf-8").read()
    # [^>]* cubre atributos extra que mete MuseScore (ej. after-barline="yes").
    txt = re.sub(r'<clef number="(\d)"[^>]*>.*?</clef>', repl, txt, flags=re.S)
    open(xml_path, "w", encoding="utf-8").write(txt)


def apply_metadata(xml_path, pdf_path, title, composer, arranger, mscore_bin):
    """Edita la metadata del MusicXML guardado y regenera el PDF (si hay MuseScore)."""
    _set_musicxml_metadata(xml_path, title, composer, arranger)
    if mscore_bin and pdf_path:
        _run_mscore(mscore_bin, xml_path, pdf_path)


# Separación de piano de alta calidad (cascada). Los modelos se cachean acá (persistente).
AUDIO_SEP_MODELS = os.path.expanduser("~/.cache/audio-separator-models")
VOCAL_ROFORMER = "mel_band_roformer_kim_ft_unwa.ckpt"  # MelBand Roformer (SOTA en voz, 12.4 SDR)


def separate_piano_hq(audio_path, work_dir):
    """Aísla el piano en dos etapas, priorizando calidad sobre velocidad:
    1) MelBand Roformer quita la voz (donde los roformer superan por lejos a Demucs);
    2) htdemucs_6s extrae el piano del instrumental ya sin voz (más limpio que sobre la mezcla).
    Devuelve el path del WAV de piano."""
    import shutil
    import uuid as _uuid

    from audio_separator.separator import Separator

    os.makedirs(AUDIO_SEP_MODELS, exist_ok=True)
    rof_dir = os.path.join(work_dir, "roformer")
    os.makedirs(rof_dir, exist_ok=True)

    sep = Separator(output_dir=rof_dir, model_file_dir=AUDIO_SEP_MODELS, log_level=40)
    sep.load_model(VOCAL_ROFORMER)
    outs = sep.separate(audio_path)  # produce (vocals) y (other=instrumental)
    inst = next((os.path.join(rof_dir, f) for f in outs
                 if "(other)" in f or "instrumental" in f.lower()), None)
    for f in outs:  # borrar el stem de voz, no se usa (§6.16)
        if "(vocals)" in f:
            try:
                os.remove(os.path.join(rof_dir, f))
            except OSError:
                pass
    if not inst:
        raise RuntimeError("no se pudo quitar la voz")

    # nombre interno UUID para el subprocess de Demucs (§6.6)
    inst_uuid = os.path.join(work_dir, _uuid.uuid4().hex + ".wav")
    shutil.copy(inst, inst_uuid)
    os.remove(inst)
    piano = separate_stems(inst_uuid, work_dir, ["piano"]).get("piano")
    if not piano:
        raise RuntimeError("no se pudo aislar el piano")
    return piano


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
    _lock_clefs(xml)                            # clave de sol arriba / fa abajo, sin cambios
    _set_musicxml_metadata(xml, title, "", "")  # título de la canción
    _run_mscore(mscore_bin, xml, pdf)           # MusicXML (final) -> PDF
    return xml, pdf
