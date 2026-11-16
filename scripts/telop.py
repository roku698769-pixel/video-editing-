#!/usr/bin/env python3
"""テロップ（ASS）を JSON から作る

使い方:
    python3 scripts/telop.py projects/<名前>/telop.json projects/<名前>/telop.ass

telop.json の例:
{
  "format": "vertical",
  "defaults": {"font": "Inter SemiBold", "size": 72, "color": "FFFFFF",
               "align": "center", "fade_in": 0.25, "fade_out": 0.2,
               "shadow": true, "anim": "rise"},
  "items": [
    {"text": "Day 1", "start": 0.4, "end": 3.0, "x": 540, "y": 820, "size": 64,
     "font": "Inter Medium"},
    {"text": "Lisbon", "start": 0.9, "end": 3.0, "x": 540, "y": 920, "size": 96}
  ]
}

- 座標は完成画面のピクセル（縦型 1080x1920／横型 1920x1080）。x,y は文字のまとまりの基準点
  （align が center なら中央、left なら左端、right なら右端。縦方向は常に中央）
- 改行は "\\N"
- anim: "none"（その場でフェード）／"rise"（フェードしながら下から 24px 上がる）
- shadow: 縁取りではなく、ぼかした影を別レイヤーで右下に置く（安っぽい縁取りを避ける）
- color は RRGGBB。ASS の色は BGR の順なので、ここで変換する
- 実際の描画幅はフォントの数値より狭くなることがあるため、
  位置合わせは必ず書き出したフレーム画像で確認する
"""
import json
import sys

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
ALIGN = {"left": 4, "center": 5, "right": 6}
DEFAULTS = {"font": "Inter SemiBold", "size": 72, "color": "FFFFFF", "align": "center",
            "fade_in": 0.25, "fade_out": 0.2, "shadow": True, "anim": "none",
            "spacing": 0.5, "rise": 24, "rise_time": 0.45}


def ts(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def bgr(hex6):
    h = hex6.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def main(src, out):
    j = json.load(open(src))
    fmt = j.get("format", "vertical")
    W, H = (fmt["w"], fmt["h"]) if isinstance(fmt, dict) else FORMATS[fmt]
    d0 = {**DEFAULTS, **j.get("defaults", {})}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Inter,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    for it in j["items"]:
        d = {**d0, **it}
        x, y = float(d["x"]), float(d["y"])
        t0, t1 = float(d["start"]), float(d["end"])
        fi, fo = int(d["fade_in"] * 1000), int(d["fade_out"] * 1000)
        base = (f"\\an{ALIGN[d['align']]}\\fn{d['font']}\\fs{d['size']}\\fsp{d['spacing']}"
                f"\\fad({fi},{fo})")

        def pos(dx, dy):
            if d["anim"] == "rise":
                rt = int(d["rise_time"] * 1000)
                return f"\\move({x + dx},{y + dy + d['rise']},{x + dx},{y + dy},0,{rt})"
            return f"\\pos({x + dx},{y + dy})"

        if d["shadow"]:
            ev.append(f"Dialogue: 0,{ts(t0)},{ts(t1)},Base,,0,0,0,,"
                      f"{{{base}{pos(3, 4)}\\1c&H000000&\\1a&H70&\\blur10}}{d['text']}")
        ev.append(f"Dialogue: 1,{ts(t0)},{ts(t1)},Base,,0,0,0,,"
                  f"{{{base}{pos(0, 0)}\\1c{bgr(d['color'])}}}{d['text']}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")
    print(f"{out}: {len(j['items'])}項目")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
