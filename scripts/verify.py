#!/usr/bin/env python3
"""完成ファイルの検証

使い方:
    python3 scripts/verify.py projects/<名前>/work/manifest.json [--at 秒,秒,...] [--silence]

- edit.py が書き出した各出力について、ffprobe -count_frames で実際のフレーム数を数え、
  設計値（manifest の total_frames）と一致するか確認する
- 確認用のフレーム画像を work/check/<出力名>/ に書き出す：
  冒頭・各クリップの切り替わり前後・末尾、および --at で指定した時刻
- 音量（統合ラウドネス LUFS とトゥルーピーク）を測る
- --silence を付けると無音区間（-45dB 以下）も一覧にする（ナレーション位置の確認用）
"""
import argparse
import json
import os
import re
import subprocess
from fractions import Fraction


def count_frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames,width,height,r_frame_rate",
                        "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    s = j["streams"][0]
    return int(s["nb_read_frames"]), s["width"], s["height"], float(j["format"]["duration"])


def color_info(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=profile,pix_fmt,color_space,color_primaries,color_transfer,color_range",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    s = json.loads(r.stdout)["streams"][0]
    tags = [s.get(k, "?") for k in ("color_space", "color_primaries", "color_transfer")]
    ok = tags == ["bt709"] * 3 and s.get("color_range") == "tv" and s.get("pix_fmt") == "yuv420p"
    return (f"{s.get('profile')} {s.get('pix_fmt')} 色空間 {'/'.join(tags)} {s.get('color_range')}"
            f"{'' if ok else ' ★想定（yuv420p・bt709・tv）と違う'}")


def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", tail)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail)
    return (i.group(1) if i else "?"), (p.group(1) if p else "?")


def silences(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
                        "-af", "silencedetect=n=-45dB:d=0.3", "-f", "null", "-"],
                       capture_output=True, text=True)
    st = re.findall(r"silence_start: (-?[\d.]+)", r.stderr)
    en = re.findall(r"silence_end: ([\d.]+)", r.stderr)
    return list(zip(st, en + ["(末尾まで)"] * (len(st) - len(en))))


def grab(path, frame, fps, out):
    """frame 番目の画像を書き出す。末尾付近でも確実に取れるよう、
    少し手前へシークしてからフレーム番号で選ぶ"""
    k0 = max(0, frame - int(round(fps)))
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{k0 / fps:.6f}", "-i", path,
                    "-vf", f"select=gte(n\\,{frame - k0})", "-frames:v", "1",
                    "-update", "1", out], check=True)
    if not os.path.exists(out):
        raise SystemExit(f"フレーム {frame} を書き出せませんでした: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--at", default="", help="追加で確認したい時刻（秒、カンマ区切り）")
    ap.add_argument("--silence", action="store_true")
    a = ap.parse_args()
    m = json.load(open(a.manifest))
    fps, total = float(Fraction(str(m["fps"]))), m["total_frames"]
    work = os.path.dirname(os.path.abspath(a.manifest))

    marks = {0: "冒頭", total - 1: "末尾"}
    for c in m["timeline"][1:]:
        s = c["start_frame"]
        marks.setdefault(max(0, s - 1), f"クリップ{c['clip']}開始の直前")
        marks.setdefault(s, f"クリップ{c['clip']}開始")
        if c["start_frame"] >= 0 and m["timeline"][c["clip"] - 1]["transition_frames"]:
            end = s + m["timeline"][c["clip"] - 1]["transition_frames"]
            marks.setdefault(end - 1, f"クリップ{c['clip']}へのトランジション最終")
    for t in filter(None, a.at.split(",")):
        marks.setdefault(int(round(float(t) * fps)), f"指定 {t}秒")

    ok_all = True
    for mode, path in m["outputs"].items():
        n, w, h, dur = count_frames(path)
        ok = n == total
        ok_all &= ok
        print(f"[{mode}] {path}")
        print(f"  フレーム数 {n}（設計 {total}）{'一致' if ok else '★不一致'}  {w}x{h}  "
              f"コンテナ尺 {dur:.3f}秒")
        print(f"  {color_info(path)}")
        lu, pk = loudness(path)
        print(f"  音量 {lu} LUFS／トゥルーピーク {pk} dBFS")
        if a.silence:
            for s, e in silences(path):
                print(f"  無音 {s}〜{e}秒")
        cdir = os.path.join(work, "check", mode)
        os.makedirs(cdir, exist_ok=True)
        for old in os.listdir(cdir):
            os.remove(os.path.join(cdir, old))
        for f in sorted(k for k in marks if 0 <= k < total):
            out = os.path.join(cdir, f"f{f:05d}.png")
            grab(path, f, fps, out)
        print(f"  確認用フレーム {len(marks)}枚 → {cdir}")
    print("\n確認用フレームの内訳:")
    for f in sorted(marks):
        if 0 <= f < total:
            print(f"  f{f:05d}（{f / fps:.3f}秒）{marks[f]}")
    if not ok_all:
        raise SystemExit("フレーム数が設計と一致しない出力があります")


if __name__ == "__main__":
    main()
