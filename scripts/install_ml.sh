#!/usr/bin/env bash
# Dependencias ML del pipeline (transcripción de piano). torch se instala CPU-only PRIMERO
# (sin CUDA, no hay GPU) para que el resto lo tome ya satisfecho.
set -euo pipefail
cd "$(dirname "$0")/.."
PIP="${1:-.venv/bin/pip}"

$PIP install -U setuptools wheel Cython
$PIP install --index-url https://download.pytorch.org/whl/cpu torch torchaudio

# Transcripción de piano (ByteDance, SOTA en piano solo) + music21 + librosa (tempo).
$PIP install piano_transcription_inference "music21==10.5.0" "librosa==0.11.0" "scipy" "resampy"

# Demucs para aislar el piano de una mezcla (opcional en la UI).
$PIP install demucs

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
