#!/usr/bin/env python3
"""素材の下調べ

使い方:
    python3 scripts/probe.py <ファイルまたはフォルダ> [...]

1素材1行で、尺・解像度（回転を反映した表示上のサイズ）・フレームレート・
可変フレームレートの疑い・HDRかどうか・音声の有無を一覧にする。
スマホ撮影の素材は「回転メタデータ」「可変フレームレート」「HDR（HLG/PQ）」の
3つが編集時の事故の原因になりやすいので、ここで先に洗い出す。
"""
import json
import os
import subprocess
import sys

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".webm", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}
HDR_TRC = {"arib-std-b67": "HDR(HLG)", "smpte2084": "HDR(PQ)"}


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def rate(s):
    try:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    except Exception:
        return 0.0


def rotation(v):
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    r = v.get("tags", {}).get("rotate")
    return int(r) if r else 0


def info(path):
    """edit.py からも使う。素材の要点を dict で返す"""
    j = ffprobe(path)
    if j is None:
        return None
    vs = [s for s in j["streams"] if s["codec_type"] == "video"
          and not s.get("disposition", {}).get("attached_pic")]
    aus = [s for s in j["streams"] if s["codec_type"] == "audio"]
    d = {"path": path, "duration": float(j["format"].get("duration", 0) or 0),
         "has_video": bool(vs), "has_audio": bool(aus)}
    if vs:
        v = vs[0]
        rot = rotation(v)
        w, h = v.get("width", 0), v.get("height", 0)
        if abs(rot) % 180 == 90:
            w, h = h, w
        r_fps, avg_fps = rate(v.get("r_frame_rate", "0/1")), rate(v.get("avg_frame_rate", "0/1"))
        d.update({
            "codec": v.get("codec_name"), "w": w, "h": h, "rotation": rot,
            "fps": avg_fps or r_fps, "r_fps": r_fps,
            "vfr_suspect": bool(r_fps and avg_fps and abs(r_fps - avg_fps) > 0.05),
            "pix_fmt": v.get("pix_fmt", ""),
            "hdr": HDR_TRC.get(v.get("color_transfer", ""), ""),
            "color_transfer": v.get("color_transfer", ""),
            "color_primaries": v.get("color_primaries", ""),
        })
    if aus:
        a = aus[0]
        d.update({"a_codec": a.get("codec_name"), "a_rate": int(a.get("sample_rate", 0) or 0),
                  "a_ch": a.get("channels", 0)})
    return d


def collect(args):
    for a in args:
        if os.path.isdir(a):
            for root, _, files in os.walk(a):
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in VIDEO_EXT | IMAGE_EXT | AUDIO_EXT:
                        yield os.path.join(root, f)
        else:
            yield a


def main(args):
    if not args:
        print(__doc__)
        sys.exit(1)
    for p in collect(args):
        d = info(p)
        if d is None:
            print(f"{p}\t読み取り不可")
            continue
        parts = [p, f"{d['duration']:.2f}s"]
        if d["has_video"]:
            parts.append(f"{d['w']}x{d['h']}")
            if d["rotation"]:
                parts.append(f"回転{d['rotation']}°")
            parts.append(f"{d['fps']:.3f}fps")
            if d["vfr_suspect"]:
                parts.append(f"可変fpsの疑い(r={d['r_fps']:.3f})")
            parts.append(f"{d['codec']}/{d['pix_fmt']}")
            if d["hdr"]:
                parts.append(d["hdr"])
        if d["has_audio"]:
            parts.append(f"音声 {d['a_codec']} {d['a_rate']}Hz {d['a_ch']}ch")
        else:
            parts.append("音声なし")
        print("\t".join(parts))


if __name__ == "__main__":
    main(sys.argv[1:])
