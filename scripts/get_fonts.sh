#!/usr/bin/env bash
# テロップ用のフォントを fonts/ に取得する（フォント本体は Git に入れない）
#
# 使い方:
#   bash scripts/get_fonts.sh                 # 既定の一式
#   bash scripts/get_fonts.sh noto-sans-jp    # 指定したものだけ
#
# 取得元は npm の @expo-google-fonts/<名前>（Google Fonts の TTF をそのまま収録。
# ライセンスは各フォントの OFL）。Google Fonts のサイトはこの環境のネットワークで
# 許可されていないが、npm のレジストリは使える。
# 名前の一覧: https://github.com/expo/google-fonts （例: zen-old-mincho, bebas-neue）
#
# 取得後、ASS で使う名前（フォントファミリー名）を一覧表示する。
set -euo pipefail
cd "$(dirname "$0")/.."

DEFAULT=(
  noto-sans-jp          # 日本語ゴシックの基本
  zen-kaku-gothic-new   # 日本語ゴシック（やわらかめ、上品）
  noto-serif-jp         # 日本語明朝の基本
  shippori-mincho       # 日本語明朝（雰囲気のある見出し向け）
  montserrat            # 英字ゴシック（見出し）
  playfair-display      # 英字セリフ（上品な見出し）
  cormorant-garamond    # 英字セリフ（細身、映画的）
  dm-serif-display      # 英字セリフ（太めの見出し）
)
if [ $# -gt 0 ]; then FONTS=("$@"); else FONTS=("${DEFAULT[@]}"); fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p fonts
for name in "${FONTS[@]}"; do
  if [ -f "fonts/.got_$name" ]; then
    echo "取得済み: $name"
    continue
  fi
  echo "取得中: $name"
  (cd "$tmp" && npm pack --silent "@expo-google-fonts/$name" >/dev/null)
  tar -xzf "$tmp"/expo-google-fonts-"$name"-*.tgz -C "$tmp"
  # libass（テロップの描画）は fontsdir の直下しか探さないので、フォルダを分けずに置く
  find "$tmp/package" -name '*.ttf' -exec cp {} fonts/ \;
  touch "fonts/.got_$name"
  rm -rf "$tmp/package" "$tmp"/*.tgz
done

echo
echo "ASS で指定する名前（ファミリー名: スタイル）:"
fc-scan --format '%{family[0]}: %{style[0]}\n' fonts/*.ttf 2>/dev/null | sort -u
