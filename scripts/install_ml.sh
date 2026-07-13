#!/usr/bin/env bash
# Dependencias ML del pipeline (paso 6+). NO se instalan con un `pip install -r` normal:
# basic-pitch 0.4.0 pinea tensorflow<2.15.1 (sin wheels para Python 3.12), así que se instala
# --no-deps sobre el backend ONNX, que no necesita TensorFlow en runtime.
# pretty_midi/mir_eval traen sdists cuyo build necesita setuptools moderno => --no-build-isolation.
set -euo pipefail
cd "$(dirname "$0")/.."
PIP="${1:-.venv/bin/pip}"

$PIP install -U setuptools wheel Cython
$PIP install --no-build-isolation "pretty_midi==0.2.11" "mir_eval==0.8.2"
$PIP install "librosa==0.11.0" "onnxruntime==1.27.0" "scipy" "resampy" "music21==10.5.0"
$PIP install --no-deps "basic-pitch==0.4.0"

echo "ML deps instaladas. Backend de inferencia: ONNX (sin TensorFlow)."

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
