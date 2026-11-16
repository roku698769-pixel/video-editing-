#!/usr/bin/env python3
"""テロップ（ASS）を JSON から作る

使い方:
    python3 scripts/telop.py projects/<名前>/telop.json projects/<名前>/telop.ass

telop.json の例:
{
  "format": "vertical",
  "fps": 30,
  "defaults": {"font": "Inter SemiBold", "size": 72, "color": "FFFFFF",
               "align": "center", "in": ["rise", "blur"], "out": ["fade"],
               "in_time": 0.6, "out_time": 0.35, "shadow": true},
  "items": [
    {"text": "Day 1", "start": 0.4, "end": 3.0, "x": 540, "y": 820, "size": 64,
     "font": "Inter Medium", "in": ["track"]},
    {"text": "Lisbon", "start": 0.9, "end": 3.0, "x": 540, "y": 920, "size": 96,
     "in": ["chars", "blur"]}
  ]
}

- 座標は完成画面のピクセル（縦型 1080x1920／横型 1920x1080）。x,y は文字のまとまりの基準点
  （align が center なら中央、left なら左端、right なら右端。縦方向は常に中央）
- fps は edit.json の出力 fps と同じにする（アニメーションを 1 フレームずつ計算するため）
- 改行は "\\N"
- 出方（in）と消え方（out）は組み合わせて指定できる（リスト）
    fade   … 透明度だけを変える
    rise   … 下から上がってくる（out では上へ抜ける）。距離は rise（既定 28px）
    blur   … ぼけた状態からピントが合う（out ではぼけながら消える）
    track  … 字間が広い状態から締まる（out では広がりながら消える）
    scale  … わずかに大きい状態から落ち着く（104% → 100%）
    chars  … 1 文字ずつ時間差で現れる（in のみ。間隔は stagger、既定 0.035 秒）
- 動きはすべてイージング（出るときは減速、消えるときは加速）をかけ、
  1 フレームごとのイベントに展開する。ASS の \\move は等速でしか動かず安っぽく見えるため使わない
- shadow: 縁取りではなく、ぼかした影を別レイヤーで右下に置く
- color は RRGGBB。ASS の色は BGR の順なので、ここで変換する
- 実際の描画幅はフォントの数値より狭くなることがあるため、
  位置合わせは必ず書き出したフレーム画像で確認する
"""
import json
import sys
from fractions import Fraction

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
ALIGN = {"left": 4, "center": 5, "right": 6}
DEFAULTS = {"font": "Inter SemiBold", "size": 72, "color": "FFFFFF", "align": "center",
            "in": ["fade"], "out": ["fade"], "in_time": 0.5, "out_time": 0.3,
            "shadow": True, "spacing": 0.5, "rise": 28, "blur": 10, "track": 14,
            "scale": 104, "stagger": 0.035, "shadow_alpha": 0x70, "shadow_blur": 10,
            "shadow_offset": [3, 4], "bold": False}


def ease_out(p):
    return 1 - (1 - p) ** 3


def ease_in(p):
    return p ** 3


def ts(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def bgr(hex6):
    h = hex6.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def alpha_hex(a):
    """a: 0（透明）〜1（不透明）を ASS の \\alpha 値に"""
    v = int(round(255 * (1 - max(0.0, min(1.0, a)))))
    return f"&H{v:02X}&"


def state(d, kinds, p):
    """p: 0（変化前）〜1（定位置・不透明）での各パラメータ"""
    s = {"a": p, "dy": 0.0, "blur": 0.0, "fsp": float(d["spacing"]), "scl": 100.0}
    q = 1 - p
    if "rise" in kinds:
        s["dy"] = d["rise"] * q
    if "blur" in kinds:
        s["blur"] = d["blur"] * q
    if "track" in kinds:
        s["fsp"] += d["track"] * q
    if "scale" in kinds:
        s["scl"] = 100 + (d["scale"] - 100) * q
    return s


def body(d, s, layer_shadow, x, y, text_override=None):
    ox, oy = (d["shadow_offset"] if layer_shadow else (0, 0))
    base_alpha = (d["shadow_alpha"] / 255) if layer_shadow else 0.0   # 影は元から半透明
    vis = s["a"] * (1 - base_alpha)
    tags = [f"\\an{ALIGN[d['align']]}", f"\\fn{d['font']}", f"\\fs{d['size']}",
            f"\\b{1 if d['bold'] else 0}",
            f"\\pos({x + ox:.2f},{y + oy + s['dy']:.2f})",
            f"\\fsp{s['fsp']:.2f}", f"\\fscx{s['scl']:.2f}\\fscy{s['scl']:.2f}"]
    if layer_shadow:
        tags.append(f"\\1c&H000000&\\blur{d['shadow_blur'] + s['blur']:.2f}")
    else:
        tags.append(f"\\1c{bgr(d['color'])}")
        if s["blur"] > 0.05:
            tags.append(f"\\blur{s['blur']:.2f}")
    if text_override is None:
        tags.append(f"\\alpha{alpha_hex(vis)}")
        return "{" + "".join(tags) + "}" + d["text"]
    return "{" + "".join(tags) + "}" + text_override(1 - base_alpha)


def chars_text(d, t_local, fps):
    """1 文字ずつ時間差で現れる文字列（インラインの透明度とぼかし）"""
    def build(max_vis):
        out = []
        i = 0
        text = d["text"]
        k = 0
        while k < len(text):
            if text.startswith("\\N", k):
                out.append("\\N")
                k += 2
                continue
            ch = text[k]
            p = (t_local - i * d["stagger"]) / d["in_time"]
            p = ease_out(max(0.0, min(1.0, p)))
            tag = f"\\alpha{alpha_hex(p * max_vis)}"
            if "blur" in d["in"]:
                tag += f"\\blur{d['blur'] * (1 - p):.2f}"
            out.append("{" + tag + "}" + ch)
            if ch != " ":
                i += 1
            k += 1
        return "".join(out)
    return build


def n_visible_chars(text):
    return len(text.replace("\\N", "").replace(" ", ""))


def item_events(d, fps):
    x, y = float(d["x"]), float(d["y"])
    t0, t1 = float(d["start"]), float(d["end"])
    fin, fout = list(d["in"]), list(d["out"])
    chars = "chars" in fin
    tin = d["in_time"] + (d["stagger"] * max(0, n_visible_chars(d["text"]) - 1) if chars else 0)
    tout = d["out_time"]
    if tin + tout > t1 - t0:
        raise SystemExit(f"「{d['text']}」: 表示時間が出方＋消え方より短いです")
    fr = 1 / fps
    ev = []

    def emit(a, b, s, txt=None):
        for shadow in ([True, False] if d["shadow"] else [False]):
            ev.append((0 if shadow else 1, a, b, body(d, s, shadow, x, y, txt)))

    # 出方：1 フレームずつ
    n_in = max(1, int(round(tin * fps)))
    for k in range(n_in):
        a, b = t0 + k * fr, t0 + (k + 1) * fr
        tl = (k + 0.5) * fr
        if chars:
            pr = ease_out(min(1.0, tl / tin))
            s = state(d, [x for x in fin if x not in ("chars", "blur")], pr)
            s["a"] = 1.0
            emit(a, b, s, chars_text(d, tl, fps))
        else:
            emit(a, b, state(d, fin, ease_out(min(1.0, tl / tin))))
    # 静止区間
    mid_a, mid_b = t0 + n_in * fr, t1 - tout
    if mid_b > mid_a:
        emit(mid_a, mid_b, state(d, [], 1.0))
    # 消え方：1 フレームずつ
    n_out = max(1, int(round(tout * fps)))
    for k in range(n_out):
        a, b = t1 - tout + k * fr, t1 - tout + (k + 1) * fr
        p = 1 - ease_in(min(1.0, (k + 0.5) * fr / tout))
        s = state(d, fout, p)
        if "rise" in fout:   # 消えるときは上へ抜ける
            s["dy"] = -s["dy"]
        emit(a, min(b, t1), s)
    return ev


def main(src, out):
    j = json.load(open(src))
    fmt = j.get("format", "vertical")
    W, H = (fmt["w"], fmt["h"]) if isinstance(fmt, dict) else FORMATS[fmt]
    fps = float(Fraction(str(j.get("fps", 30))))
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
    lines = []
    for it in j["items"]:
        d = {**d0, **it}
        for key in ("in", "out"):
            if isinstance(d[key], str):
                d[key] = [d[key]]
        for layer, a, b, text in item_events(d, fps):
            if ts(b) == ts(a):
                continue
            lines.append(f"Dialogue: {layer},{ts(a)},{ts(b)},Base,,0,0,0,,{text}")
    with open(out, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")
    print(f"{out}: {len(j['items'])}項目（イベント {len(lines)}行）")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
