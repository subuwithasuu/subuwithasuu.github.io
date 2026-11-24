#!/usr/bin/env bash
# Downloads free assets the pipeline needs (used locally and in GitHub Actions).
#  - Google Fonts (SIL Open Font License) into ./fonts
#  - Kokoro TTS model files into $KOKORO_DIR (default ./models) unless already present
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p fonts
GF="https://github.com/google/fonts/raw/main/ofl"
fetch() { [ -s "fonts/$2" ] || curl -fsSL "$GF/$1/$2" -o "fonts/$2"; }
fetch anton            Anton-Regular.ttf
fetch dmserifdisplay   DMSerifDisplay-Regular.ttf
fetch lato             Lato-Bold.ttf
fetch lato             Lato-Regular.ttf
fetch lato             Lato-Black.ttf
fetch pacifico         Pacifico-Regular.ttf
echo "fonts ok"

if [ "${SKIP_KOKORO:-0}" != "1" ]; then
  KD="${KOKORO_DIR:-models}"
  mkdir -p "$KD"
  KR="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
  [ -s "$KD/kokoro-v1.0.onnx" ] || curl -fsSL "$KR/kokoro-v1.0.onnx" -o "$KD/kokoro-v1.0.onnx"
  [ -s "$KD/voices-v1.0.bin" ]  || curl -fsSL "$KR/voices-v1.0.bin"  -o "$KD/voices-v1.0.bin"
  echo "kokoro ok ($KD)"
fi
