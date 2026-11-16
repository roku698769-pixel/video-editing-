#!/usr/bin/env python3
"""色・HDR変換などの設定違いを、同じ1フレームで並べて比べる

使い方:
    python3 scripts/compare.py projects/<名前>/edit.json <クリップ番号> <クリップ内の秒> \\
        --variants projects/<名前>/variants.json [--out 出力.png]

variants.json の例（各項目は、クリップの設定・プロジェクトの設定を上書きする）:
[
  {"label": "hable",    "project": {"tonemap": {"method": "hable"}}},
  {"label": "mobius",   "project": {"tonemap": {"method": "mobius"}}},
  {"label": "clip",     "project": {"tonemap": {"method": "clip"}}},
  {"label": "grade A",  "clip": {"grade": {"contrast": 0.3, "saturation": 1.1}}},
  {"label": "無補正",    "clip": {"grade": false}}
]

- 本番と同じ下ごしらえの処理（edit.py の video_graph）を通すので、見えるものは本番と同じ
  （手ブレ補正は 1 フレームでは比べられないため外す）
- 結果は横に並べた PNG（各画像の上にラベル）。既定の出力先は work/compare/
- HDR素材の変換方式とカラーグレーディングは、本物の素材でこれを使って決める
"""
import argparse
import copy
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edit  # noqa: E402

LABEL_FONT = "/usr/share/fonts/opentype/inter/Inter-Medium.otf"


def render_one(cfg, clip, t, out):
    clip.c = dict(clip.c)
    clip.c.pop("stabilize", None)
    inp, fc, lab = edit.video_graph(clip, cfg)
    # 入力の -ss / -t をクリップ内の時刻 t に合わせて差し替える
    if not clip.is_image:
        i = inp.index("-ss")
        inp[i + 1] = f"{clip.t_in + t * clip.speed:.6f}"
        j = inp.index("-t")
        inp[j + 1] = f"{max(0.5, 4 / float(cfg.fps)):.6f}"
    fc += f";[{lab}]scale=in_color_matrix=bt709:out_color_matrix=bt709,format=rgb48be[o]"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inp,
                    "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", out], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_json")
    ap.add_argument("clip", type=int)
    ap.add_argument("t", type=float)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--out")
    ap.add_argument("--height", type=int, default=960, help="並べるときの1枚の高さ（px）")
    a = ap.parse_args()
    variants = json.load(open(a.variants))
    base = edit.Cfg(a.edit_json, None)
    os.makedirs(base.work, exist_ok=True)
    clips = edit.load_clips(base)
    outdir = os.path.join(base.work, "compare")
    os.makedirs(outdir, exist_ok=True)
    pngs = []
    for n, v in enumerate(variants):
        cfg = copy.copy(base)
        for k, val in (v.get("project") or {}).items():
            setattr(cfg, k, val)
        clip = copy.copy(clips[a.clip])
        clip.c = {**clip.c, **(v.get("clip") or {})}
        p = os.path.join(outdir, f"v{n:02d}.png")
        print(f"[{n}] {v.get('label', n)}")
        render_one(cfg, clip, a.t, p)
        pngs.append((p, v.get("label", str(n))))
    out = a.out or os.path.join(outdir, f"compare_clip{a.clip:02d}_{a.t:.2f}s.png")
    ins, parts = [], []
    for k, (p, label) in enumerate(pngs):
        ins += ["-i", p]
        lab = label.replace("'", "").replace(":", "\\:")
        parts.append(f"[{k}:v]scale=-2:{a.height}:flags=lanczos,format=rgb24,"
                     f"pad=iw:ih+56:0:56:color=0x111111,"
                     f"drawtext=fontfile={LABEL_FONT}:text='{lab}':fontsize=30:"
                     f"fontcolor=white:x=16:y=12[p{k}]")
    stack = "".join(f"[p{k}]" for k in range(len(pngs)))
    fc = ";".join(parts) + (f";{stack}hstack=inputs={len(pngs)}[o]" if len(pngs) > 1
                            else ";[p0]null[o]")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *ins,
                    "-filter_complex", fc, "-map", "[o]", "-frames:v", "1", out], check=True)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
