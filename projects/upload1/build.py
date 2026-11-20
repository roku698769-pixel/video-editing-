#!/usr/bin/env python3
"""upload1（ニュース風）の編集指示を組み立てる。
各区間の開始フレームをここで一度だけ計算し、
  - edit.json（映像・ナレーション・BGM の配置）
  - work/timeline.json（HyperFrames のグラフィックと BGM の効果音のタイミング）
を書き出す。
使い方: python3 projects/upload1/build.py [--sr]   （--sr で高解像度化した素材を使う）
"""
import json, os, sys
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
P = "projects/upload1"
F = Fraction(30000, 1001)
SR = "--sr" in sys.argv
SRC = "footage/upload1/sr.mkv" if SR else "footage/upload1/source.MP4"
STILL = "projects/upload1/work/sr_png" if SR else "projects/upload1/work/src_png"


def fr(sec):
    return int(round(sec * float(F)))


def t(frames):
    return float(frames / F)


# 拡大（静止したクロップ。1080x1920 の座標で、9:16 を保つ）
def zoom(x, y, w):
    h = round(w * 16 / 9)
    return f"crop={w}:{h}:{x}:{y},scale=1080:1920:flags=lanczos+accurate_rnd+full_chroma_int"


Z_WIDE = zoom(150, 180, 675)     # 引きの場面（素材の0〜10秒）を1.6倍に
Z_CLOSE2 = zoom(0, 100, 900)     # 14秒台（ガムを奪う）1.2倍
Z_CLOSE3 = zoom(180, 100, 900)   # 15秒台（ガムが落ちる）1.2倍

clips = []
sections = {}


def add(name, c, frames):
    start = sum(x[1] for x in clips)
    clips.append((c, frames))
    sections.setdefault(name, start)
    return start


def video(name, a, b, speed=1.0, **kw):
    c = {"src": SRC, "in": a, "out": b}
    if speed != 1.0:
        c["speed"] = speed
    c.update(kw)
    return add(name, c, int(round((b - a) / speed * float(F))))


def still(name, frame_idx, dur, vf):
    c = {"src": f"{STILL}/f{frame_idx:04d}.png", "dur": dur, "fit": "fill", "vf": vf}
    return add(name, c, fr(dur))


# ① 何があったか：現場の映像をぼかして暗くした背景（グラフィックが上に乗る）
video("open", 0.0, 12.8, volume=0, vf="gblur=sigma=28,eq=brightness=-0.06:saturation=0.85")
# ② 実際の映像（素材の全体）
video("main", 0.0, 25.33, volume=2.0, denoise=10, highpass=90)
# ③ スロー
video("r1", 3.0, 5.7, speed=0.4, interp="motion", volume=0, vf=Z_WIDE)
video("r2", 13.95, 14.52, speed=0.3, interp="motion", volume=0, vf=Z_CLOSE2)
still("r2f", 435, 2.25, Z_CLOSE2)            # ガムを掲げた瞬間で止める
video("r3", 15.40, 15.80, speed=0.25, interp="motion", volume=0, vf=Z_CLOSE3)
still("r3f", 474, 2.1, Z_CLOSE3)           # ガムが落ちていく瞬間で止める
# ④ 締め
video("close", 18.8, 25.33, volume=1.0, denoise=10, highpass=90)
still("closef", 758, 0.9, "null")

total = sum(x[1] for x in clips)
S = {k: t(v) for k, v in sections.items()}
S["end"] = t(total)

# ナレーション（区間の開始からの秒数）
NARR = {
    "n1": S["open"] + 0.55,
    "n2": S["main"] + 0.5,
    "n3": S["main"] + 10.95,
    "n4": S["main"] + 19.2,
    "n5": S["r1"] + 0.2,
    "n6": S["r2"] + 0.2,
    "n7": S["r3"] + 0.05,
    "n8": S["close"] + 0.2,
}

# 見出しの切り替え（本編の素材の秒数 → 完成版の秒数）
M = lambda s: S["main"] + s
timeline = {
    "fps": "30000/1001", "total": S["end"], "sections": S,
    "narration": NARR,
    "main_head2": M(13.85), "main_head3": M(19.0),
    # スローの止め絵でガムを示す丸（1080x1920 の座標）
    "circle_r2": {"t": S["r2f"], "x": round((206 - 0) * 1.2), "y": round((496 - 100) * 1.2)},
    "circle_r3": {"t": S["r3f"], "x": round((784 - 180) * 1.2), "y": round((854 - 100) * 1.2)},
}

ej = {
    "name": "upload1_news",
    "format": "vertical",
    "fps": "30000/1001",
    "grade": {"contrast": 0.1, "saturation": 1.03, "vibrance": 0.1, "sharpen": 0.0 if SR else 0.15},
    "loudness": {"i": -14, "tp": -1.0},
    "overlays": [{"src": f"{P}/work/overlay.mov", "start": 0}],
    "clips": [c for c, _ in clips],
    "audio": [{"src": "footage/upload1/bgm_mix.wav", "start": 0, "volume_db": 0}]
             + [{"src": f"footage/upload1/narr/{k}.wav", "start": round(v, 3), "volume_db": 0} for k, v in NARR.items()],
}
json.dump(ej, open(os.path.join(ROOT, P, "edit.json"), "w"), ensure_ascii=False, indent=1)
os.makedirs(os.path.join(ROOT, P, "work"), exist_ok=True)
json.dump(timeline, open(os.path.join(ROOT, P, "work", "timeline.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps(timeline, ensure_ascii=False, indent=1))
