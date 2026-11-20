#!/usr/bin/env bash
# 編集の道具を入れる：FFmpeg（確認のみ）・yt-dlp・Whisper（faster-whisper）・Remotion・HyperFrames
# 使い方: bash scripts/setup_tools.sh [--model]   （--model で Whisper large-v3 のモデル約2.9GBも先に取得する）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== FFmpeg"
command -v ffmpeg >/dev/null && ffmpeg -version | head -1 || { echo "ffmpeg がありません（apt-get install -y ffmpeg）"; exit 1; }

echo "== Python の道具（yt-dlp、faster-whisper）"
pip install -q -U yt-dlp faster-whisper
echo "yt-dlp $(yt-dlp --version) / faster-whisper $(python3 -c 'import faster_whisper; print(faster_whisper.__version__)')"

echo "== Remotion・HyperFrames（motion/）"
(cd motion && npm ci --no-audit --no-fund --loglevel=error)
(cd motion && npx hyperframes telemetry disable >/dev/null 2>&1 || true)   # 利用状況の送信を止める
echo "remotion $(node -p "require('./motion/node_modules/remotion/package.json').version") / hyperframes $(node -p "require('./motion/node_modules/hyperframes/package.json').version")"

if [[ "${1:-}" == "--model" ]]; then
  echo "== Whisper large-v3 のモデル（models/whisper/）"
  python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8', download_root='models/whisper')"
fi
echo "完了"
