#!/usr/bin/env bash
# Dependencias ML del pipeline (transcripción de piano). torch se instala CPU-only PRIMERO
# (sin CUDA, no hay GPU) para que el resto lo tome ya satisfecho.
set -euo pipefail
cd "$(dirname "$0")/.."
PIP="${1:-.venv/bin/pip}"

# setuptools<81: Transkun aún importa pkg_resources, que setuptools 81+ removió.
$PIP install -U "setuptools<81" wheel Cython
$PIP install --index-url https://download.pytorch.org/whl/cpu torch torchaudio

# Transcripción de piano (ByteDance, SOTA en piano solo) + music21 + librosa (tempo).
$PIP install piano_transcription_inference "music21==10.5.0" "librosa==0.11.0" "scipy" "resampy"

# Motor de transcripción alternativo (opcional, para A/B de calidad): Transkun, open source,
# CLI `transkun in.wav out.mid`. Baja su checkpoint la primera vez. Ver TRANSCRIPTION_BACKENDS.md.
$PIP install transkun

# Separación de piano (opcional en la UI). Cascada de alta calidad:
#   audio-separator (MelBand Roformer, SOTA en voz) quita la voz + Demucs saca el piano.
$PIP install demucs "audio-separator"

# Pre-descargar el modelo roformer (~913 MB) a la cache persistente.
.venv/bin/python - <<'PYEOF' || true
import os
from audio_separator.separator import Separator
d = os.path.expanduser("~/.cache/audio-separator-models"); os.makedirs(d, exist_ok=True)
Separator(model_file_dir=d, log_level=40).load_model("mel_band_roformer_kim_ft_unwa.ckpt")
print("roformer descargado")
PYEOF

# Cola de jobs
$PIP install "rq==2.6.0"

# Checkpoint del modelo de piano (~165 MB) desde Zenodo, cacheado en ~/piano_transcription_inference_data
CKPT_DIR="$HOME/piano_transcription_inference_data"
CKPT="$CKPT_DIR/note_F1=0.9677_pedal_F1=0.9186.pth"
if [ ! -s "$CKPT" ]; then
  mkdir -p "$CKPT_DIR"
  curl -L -C - "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1" -o "$CKPT"
fi

# yt-dlp pineado. IMPORTANTE (§6.8): actualizarlo seguido — YouTube cambia el player cada
# pocas semanas y un yt-dlp viejo se rompe ("Requested format is not available") y acumula
# CVEs. Actualizá con:  pip install -U yt-dlp
$PIP install "yt-dlp==2026.7.4"

echo "ML deps instaladas. Transcripción: modelo de piano (ByteDance). Aislar piano: Demucs/torch CPU."

# MuseScore 4 para export PDF (headless). El AppImage se extrae sin root ni FUSE.
# Se usa MuseScore 4 (no 3.6.2) porque su AppImage incluye el plugin Qt offscreen.
MS_DIR="$HOME/.local/opt/musescore-4.4.2"
if [ ! -x "$MS_DIR/AppRun" ]; then
  MS_URL="https://github.com/musescore/MuseScore/releases/download/v4.4.2/MuseScore-Studio-4.4.2.242570931-x86_64.AppImage"
  tmp="$(mktemp -d)"
  curl -sL -o "$tmp/ms.AppImage" "$MS_URL"
  chmod +x "$tmp/ms.AppImage"
  (cd "$tmp" && ./ms.AppImage --appimage-extract >/dev/null)
  mkdir -p "$(dirname "$MS_DIR")"
  rm -rf "$MS_DIR"; mv "$tmp/squashfs-root" "$MS_DIR"
  rm -rf "$tmp"
fi
echo "MuseScore listo. Poné en tu .env:  MSCORE_BIN=$MS_DIR/AppRun"

# Soundfont de piano para el reproductor de MIDI del navegador (self-hosteado, sin CDN).
# ~264 samples (3 capas de velocidad), ~9 MB. Solo se baja si falta.
SF="app/static/soundfont"; INST="$SF/acoustic_grand_piano"
if [ ! -f "$INST/p60_v79.mp3" ]; then
  mkdir -p "$INST"
  B="https://storage.googleapis.com/magentadata/js/soundfonts/sgm_plus/acoustic_grand_piano"
  echo '{"name":"clavis_piano","instruments":{"0":"acoustic_grand_piano"}}' > "$SF/soundfont.json"
  curl -s "$B/instrument.json" -o "$INST/instrument.json"
  .venv/bin/python -c "import json;p='$INST/instrument.json';d=json.load(open(p));d['velocities']=[31,79,111];json.dump(d,open(p,'w'))"
  for p in $(seq 21 108); do for v in 31 79 111; do echo "$B/p${p}_v${v}.mp3 $INST/p${p}_v${v}.mp3"; done; done \
    | xargs -P 12 -n 2 sh -c 'curl -sfL "$0" -o "$1"'
  echo "Soundfont de piano descargado ($(ls "$INST"/*.mp3 | wc -l) samples)."
fi
