#!/usr/bin/env python3
"""編集指示（edit.json）から動画を書き出す

使い方:
    python3 scripts/edit.py projects/<名前>/edit.json [--draft] [--no-light] [--clean]

  --draft     確認用の軽い版だけを作る（半分の解像度・高速設定）。構成やタイミングの確認用
  --no-light  投稿用（軽量版）を作らず、マスターだけ作る
  --clean     作業フォルダ（キャッシュ）を消してから作り直す

処理の流れ（事故防止のため、長い単一パスのエンコードはしない）:
  1. クリップごとに「下ごしらえ」した中間ファイルを作る
     （回転・HDR→SDR変換・固定フレームレート化・速度変更・画面サイズへの合わせ込み・色）
     中間ファイルは設定が変わらない限り再利用する
  2. 中間ファイルから区間ごとのチャンクをエンコードする
     （クリップ本体／トランジション。テロップ(ASS)はここで焼き込む）
  3. チャンクを concat demuxer で -c copy 結合
  4. 音声はクリップ音声と外部音声を 48kHz WAV で組み立ててから最後に mux
  5. 想定フレーム数などを work/manifest.json に書き出す（verify.py が使う）

edit.json の書式は CLAUDE.md の「edit.json の書式」を参照。
"""
import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import info as probe_info, IMAGE_EXT  # noqa: E402

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
SR = 48000
INTER_CRF = 10           # 中間ファイル（見た目では劣化しない水準）
CACHE_VERSION = 2        # 下ごしらえの処理を変えたら上げる（キャッシュを無効にする）

ENC = {
    "master": ["-c:v", "libx264", "-preset", "slow", "-crf", "16"],
    "light": ["-c:v", "libx264", "-preset", "slow", "-crf", "21", "-maxrate", "14M", "-bufsize", "28M"],
    "draft": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"],
}
ABR = {"master": "256k", "light": "192k", "draft": "128k"}


def run(cmd, quiet=True):
    print("  $ " + " ".join(shlex.quote(c) for c in cmd)[:400], flush=True)
    r = subprocess.run(cmd, capture_output=quiet, text=True)
    if r.returncode != 0:
        if quiet:
            sys.stderr.write(r.stderr[-4000:])
        raise SystemExit(f"ffmpeg が失敗しました（終了コード {r.returncode}）")


def fesc(path):
    """フィルタ引数用のパスのエスケープ"""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def atempo_chain(speed):
    """atempo は 1段あたり 0.5〜100。0.5 未満は複数段に分ける"""
    parts, s = [], speed
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def reframe(mode, W, H, fx, fy):
    sc = "flags=lanczos"
    if mode == "fit":
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease:{sc},"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    if mode == "blur":
        w4, h4 = W // 4, H // 4
        return (f"split[fg][bg];"
                f"[bg]scale={w4}:{h4}:force_original_aspect_ratio=increase,crop={w4}:{h4},"
                f"gblur=sigma=10,scale={W}:{H}:{sc},eq=brightness=-0.06:saturation=0.9[bgo];"
                f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease:{sc}[fgo];"
                f"[bgo][fgo]overlay=(W-w)/2:(H-h)/2,setsar=1")
    # fill（既定）：はみ出した分を切る。fx/fy は切り出し位置（0=左/上、0.5=中央、1=右/下）
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase:{sc},"
            f"crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy},setsar=1")


TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


class Clip:
    def __init__(self, i, c, cfg):
        self.i = i
        self.c = c
        self.src = c["src"]
        if not os.path.exists(self.src):
            raise SystemExit(f"クリップ{i}: 素材が見つかりません: {self.src}")
        self.is_image = os.path.splitext(self.src)[1].lower() in IMAGE_EXT
        self.meta = probe_info(self.src) or {}
        self.speed = float(c.get("speed", 1.0))
        F = cfg.fps
        if self.is_image:
            if "dur" not in c:
                raise SystemExit(f"クリップ{i}: 静止画には dur（秒）が必要です")
            self.t_in, self.src_len = 0.0, float(c["dur"])
            self.speed = 1.0
        else:
            self.t_in = float(c.get("in", 0.0))
            t_out = c.get("out")
            t_out = float(t_out) if t_out is not None else self.meta.get("duration", 0.0)
            if t_out <= self.t_in:
                raise SystemExit(f"クリップ{i}: out が in 以下です")
            self.src_len = t_out - self.t_in
        self.frames = int(round(self.src_len / self.speed * F))
        tr = c.get("transition") or {}
        self.tr_type = tr.get("type", "cut")
        self.tr_frames = 0 if self.tr_type == "cut" else int(round(float(tr.get("dur", 0.5)) * F))
        self.head = 0   # 前のクリップからのトランジションで使う先頭フレーム数（後で設定）

    @property
    def tail(self):
        return self.tr_frames

    def key(self, cfg):
        st = os.stat(self.src)
        blob = json.dumps({
            "v": CACHE_VERSION, "c": {k: v for k, v in self.c.items() if k != "transition"},
            "W": cfg.W, "H": cfg.H, "F": cfg.fps, "look": cfg.look,
            "size": st.st_size, "mtime": int(st.st_mtime)}, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]


class Cfg:
    def __init__(self, path, args):
        self.path = path
        with open(path) as f:
            j = json.load(f)
        self.j = j
        self.name = j.get("name") or os.path.basename(os.path.dirname(os.path.abspath(path)))
        fmt = j.get("format", "vertical")
        if isinstance(fmt, dict):
            self.W, self.H = int(fmt["w"]), int(fmt["h"])
        else:
            self.W, self.H = FORMATS[fmt]
        self.fps = int(j.get("fps", 30))
        self.look = j.get("look", "")
        self.subs = j.get("subtitles")
        self.fontsdir = j.get("fontsdir", "fonts")
        self.chunk_max = float(j.get("chunk_max", 20))
        self.clip_audio = j.get("clip_audio", True)
        self.audio = j.get("audio", [])
        self.work = os.path.join(os.path.dirname(os.path.abspath(path)), "work")
        self.out_dir = j.get("output_dir", "output")
        self.args = args


def prepare(clip, cfg):
    """クリップを出力サイズ・固定fpsの中間ファイルにする（キャッシュあり）"""
    W, H, F = cfg.W, cfg.H, cfg.fps
    c = clip.c
    out = os.path.join(cfg.work, "prep", f"clip{clip.i:02d}_{clip.key(cfg)}.mkv")
    if os.path.exists(out):
        print(f"[下ごしらえ] クリップ{clip.i}: キャッシュを使用")
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    chain = []
    if clip.meta.get("hdr") and c.get("tonemap", True):
        chain.append(TONEMAP)
    if clip.is_image:
        inp = ["-loop", "1", "-framerate", str(F), "-t", f"{clip.src_len:.6f}", "-i", clip.src]
    else:
        inp = ["-ss", f"{clip.t_in:.6f}", "-t", f"{clip.src_len:.6f}", "-i", clip.src]
        chain.append(f"setpts=(PTS-STARTPTS)/{clip.speed:.6f}")
    chain.append(f"fps={F}")
    chain.append(reframe(c.get("fit", "fill"), W, H, c.get("focus_x", 0.5), c.get("focus_y", 0.5)))
    if cfg.look and c.get("look", True):
        chain.append(cfg.look)
    if c.get("vf"):
        chain.append(c["vf"])
    chain.append("format=yuv420p")
    chain.append("tpad=stop_mode=clone:stop_duration=2")   # 足りない場合は最終フレームで埋める
    fc = "[0:v]" + ",".join(chain) + "[v]"
    print(f"[下ごしらえ] クリップ{clip.i}: {clip.src}  {clip.frames}フレーム")
    tmp = out + ".part.mkv"
    run(["ffmpeg", "-y", "-hide_banner", *inp, "-filter_complex", fc, "-map", "[v]",
         "-frames:v", str(clip.frames), "-c:v", "libx264", "-preset", "medium",
         "-crf", str(INTER_CRF), "-pix_fmt", "yuv420p", "-an", tmp])
    os.replace(tmp, out)
    return out


def sub_filter(cfg, offset_frames):
    if not cfg.subs:
        return ""
    off = offset_frames / cfg.fps
    fd = f":fontsdir={fesc(cfg.fontsdir)}" if os.path.isdir(cfg.fontsdir) else ""
    return (f",setpts=PTS+{off:.6f}/TB,subtitles=filename={fesc(cfg.subs)}{fd},"
            f"setpts=PTS-STARTPTS")


def encode_chunk(cfg, mode, inputs, fc, nframes, out):
    W, H = cfg.W, cfg.H
    scale = f",scale={W // 2}:{H // 2}:flags=bicubic" if mode == "draft" else ""
    fc = fc + scale + ",format=yuv420p[v]"
    tscale = str(cfg.fps * 512)
    ins = []
    for p in inputs:
        ins += ["-i", p]
    tmp = out + ".part.mp4"
    run(["ffmpeg", "-y", "-hide_banner", *ins, "-filter_complex", fc, "-map", "[v]",
         "-frames:v", str(nframes), *ENC[mode], "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
         "-video_track_timescale", tscale, "-an", tmp])
    os.replace(tmp, out)


def build_chunks(cfg, clips, preps, mode):
    """チャンク一覧を作ってエンコードし、ファイルのリストを返す"""
    cdir = os.path.join(cfg.work, f"chunks_{mode}")
    shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(cdir)
    files, pos = [], 0
    for k, clip in enumerate(clips):
        a, b = clip.head, clip.frames - clip.tail
        # 本体（長い場合は分割）
        step = max(1, int(cfg.chunk_max * cfg.fps))
        s = a
        while s < b:
            e = min(b, s + step)
            out = os.path.join(cdir, f"{len(files):03d}_clip{clip.i:02d}_{s}-{e}.mp4")
            fc = (f"[0:v]trim=start_frame={s}:end_frame={e},setpts=PTS-STARTPTS"
                  + sub_filter(cfg, pos))
            print(f"[チャンク {mode}] クリップ{clip.i} 本体 {s}〜{e}")
            encode_chunk(cfg, mode, [preps[k]], fc, e - s, out)
            files.append(out)
            pos += e - s
            s = e
        # 次のクリップへのトランジション
        if clip.tail and k + 1 < len(clips):
            nxt, d = clips[k + 1], clip.tail
            out = os.path.join(cdir, f"{len(files):03d}_tr{clip.i:02d}-{nxt.i:02d}.mp4")
            fc = (f"[0:v]trim=start_frame={clip.frames - d}:end_frame={clip.frames},"
                  f"setpts=PTS-STARTPTS[a];"
                  f"[1:v]trim=start_frame=0:end_frame={d},setpts=PTS-STARTPTS[b];"
                  f"[a][b]xfade=transition={clip.tr_type}:duration={d / cfg.fps:.6f}:offset=0"
                  + sub_filter(cfg, pos))
            print(f"[チャンク {mode}] トランジション {clip.tr_type} {d}フレーム")
            encode_chunk(cfg, mode, [preps[k], preps[k + 1]], fc, d, out)
            files.append(out)
            pos += d
    return files, pos


def build_audio(cfg, clips, total):
    """クリップ音声と外部音声を 48kHz WAV に組み立てる"""
    F = cfg.fps
    adir = os.path.join(cfg.work, "audio")
    os.makedirs(adir, exist_ok=True)
    tracks = []   # (wavパス, 開始秒)
    start = 0
    for clip in clips:
        dur = clip.frames / F
        out = os.path.join(adir, f"clip{clip.i:02d}.wav")
        vol = float(clip.c.get("volume", 1.0))
        use = cfg.clip_audio and not clip.is_image and clip.meta.get("has_audio") and vol > 0
        if use:
            af = [atempo_chain(clip.speed)] if abs(clip.speed - 1) > 1e-6 else []
            af += [f"volume={vol}", f"aresample={SR}",
                   "aformat=sample_fmts=fltp:channel_layouts=stereo",
                   "apad", f"atrim=0:{dur:.6f}", "asetpts=PTS-STARTPTS"]
            # トランジション部分はクロスフェード、カット部分は数msのフェードでプチ音を防ぐ
            fin = clip.head / F if clip.head else 0.008
            fout = clip.tail / F if clip.tail else 0.008
            af.append(f"afade=t=in:st=0:d={fin:.6f}:curve=qsin")
            af.append(f"afade=t=out:st={dur - fout:.6f}:d={fout:.6f}:curve=qsin")
            run(["ffmpeg", "-y", "-hide_banner", "-ss", f"{clip.t_in:.6f}",
                 "-t", f"{clip.src_len:.6f}", "-i", clip.src, "-vn",
                 "-af", ",".join(af), "-c:a", "pcm_s24le", out])
            tracks.append((out, start / F))
        start += clip.frames - clip.tail
    for n, a in enumerate(cfg.audio):
        out = os.path.join(adir, f"ext{n:02d}.wav")
        t_in = float(a.get("in", 0.0))
        inp = ["-ss", f"{t_in:.6f}"]
        if a.get("out") is not None:
            length = float(a["out"]) - t_in
            inp += ["-t", f"{length:.6f}"]
        else:
            length = (probe_info(a["src"]) or {}).get("duration", 0.0) - t_in
        length = min(length, total / F - float(a.get("start", 0.0)))
        af = [f"aresample={SR}", "aformat=sample_fmts=fltp:channel_layouts=stereo",
              f"volume={float(a.get('volume_db', 0.0))}dB", f"atrim=0:{length:.6f}"]
        if a.get("fade_in"):
            af.append(f"afade=t=in:st=0:d={float(a['fade_in'])}")
        if a.get("fade_out"):
            fo = float(a["fade_out"])
            af.append(f"afade=t=out:st={max(0.0, length - fo):.6f}:d={fo}")
        run(["ffmpeg", "-y", "-hide_banner", *inp, "-i", a["src"], "-vn",
             "-af", ",".join(af), "-c:a", "pcm_s24le", out])
        tracks.append((out, float(a.get("start", 0.0))))
    mix = os.path.join(adir, "mix.wav")
    tot = total / F
    if not tracks:
        run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi", "-i",
             f"anullsrc=r={SR}:cl=stereo", "-t", f"{tot:.6f}", "-c:a", "pcm_s24le", mix])
        return mix
    ins, parts = [], []
    for k, (p, st) in enumerate(tracks):
        ins += ["-i", p]
        parts.append(f"[{k}:a]adelay=delays={int(round(st * 1000))}:all=1[a{k}]")
    labels = "".join(f"[a{k}]" for k in range(len(tracks)))
    fc = (";".join(parts) + f";{labels}amix=inputs={len(tracks)}:normalize=0:dropout_transition=0,"
          f"apad,atrim=0:{tot:.6f}[out]")
    run(["ffmpeg", "-y", "-hide_banner", *ins, "-filter_complex", fc, "-map", "[out]",
         "-c:a", "pcm_s24le", mix])
    return mix


def assemble(cfg, files, mix, mode, out):
    lst = os.path.join(cfg.work, f"concat_{mode}.txt")
    with open(lst, "w") as f:
        for p in files:
            f.write(f"file '{os.path.abspath(p)}'\n")
    vid = os.path.join(cfg.work, f"video_{mode}.mp4")
    run(["ffmpeg", "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", vid])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".part.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-i", vid, "-i", mix, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", ABR[mode], "-ar", str(SR),
         "-movflags", "+faststart", tmp])
    os.replace(tmp, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_json")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-light", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()
    cfg = Cfg(args.edit_json, args)
    if args.clean:
        shutil.rmtree(cfg.work, ignore_errors=True)
    os.makedirs(cfg.work, exist_ok=True)
    if cfg.subs and not os.path.exists(cfg.subs):
        raise SystemExit(f"テロップファイルが見つかりません: {cfg.subs}")

    clips = [Clip(i, c, cfg) for i, c in enumerate(cfg.j["clips"])]
    if clips:
        clips[-1].tr_frames = 0          # 最後のクリップの後ろにトランジションはない
        clips[-1].tr_type = "cut"
    for k in range(1, len(clips)):
        clips[k].head = clips[k - 1].tail
    for clip in clips:
        if clip.head + clip.tail > clip.frames:
            raise SystemExit(f"クリップ{clip.i}: トランジションがクリップより長くなっています"
                             f"（{clip.head}+{clip.tail} > {clip.frames}フレーム）")

    preps = [prepare(c, cfg) for c in clips]
    total_expected = sum(c.frames - c.tail for c in clips)

    modes = ["draft"] if args.draft else (["master"] if args.no_light else ["master", "light"])
    mix = build_audio(cfg, clips, total_expected)
    outputs = {}
    for mode in modes:
        files, total = build_chunks(cfg, clips, preps, mode)
        assert total == total_expected, (total, total_expected)
        suffix = {"master": "master", "light": "post", "draft": "draft"}[mode]
        out = os.path.join(cfg.out_dir, f"{cfg.name}_{suffix}.mp4")
        assemble(cfg, files, mix, mode, out)
        outputs[mode] = out

    # 時間軸（各クリップが完成版の何フレーム目から始まるか）
    timeline, pos = [], 0
    for c in clips:
        # pos はクリップの先頭が完成版に現れるフレーム（前とのトランジションが始まる位置）
        timeline.append({"clip": c.i, "src": c.src, "start_frame": pos,
                         "frames": c.frames, "transition_out": c.tr_type,
                         "transition_frames": c.tail})
        pos += c.frames - c.tail
    manifest = {"name": cfg.name, "fps": cfg.fps, "w": cfg.W, "h": cfg.H,
                "total_frames": total_expected, "duration": total_expected / cfg.fps,
                "outputs": outputs, "timeline": timeline,
                "draft_size": [cfg.W // 2, cfg.H // 2]}
    with open(os.path.join(cfg.work, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n完了: {total_expected}フレーム（{total_expected / cfg.fps:.3f}秒）")
    for m, p in outputs.items():
        print(f"  {m}: {p}")


if __name__ == "__main__":
    main()
