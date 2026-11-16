#!/usr/bin/env python3
"""編集指示（edit.json）から動画を書き出す

使い方:
    python3 scripts/edit.py projects/<名前>/edit.json [--draft] [--no-light] [--clean]

  --draft     確認用の軽い版だけを作る（半分の解像度・高速設定）。構成やタイミングの確認用
  --no-light  投稿用（軽量版）を作らず、マスターだけ作る
  --clean     作業フォルダ（キャッシュ）を消してから作り直す

処理の流れ（画質を落とさず、事故も起こさないための構成）:
  1. クリップごとに「下ごしらえ」した中間ファイルを作る
     HDR→SDR変換 → 手ブレ補正 → 速度変更 → フレームレート変換 → 画面サイズへの合わせ込み
     → カラーグレーディング（RGB 16bit で処理）
     中間ファイルは 10bit 4:4:4 の可逆圧縮（劣化なし）。設定が変わらない限り再利用する
  2. 中間ファイルから区間ごとのチャンクをエンコードする（クリップ本体／トランジション）
     テロップ(ASS)はここで焼き込む。8bit への変換は誤差拡散ディザで行い、グラデーションの段差を防ぐ
  3. チャンクを concat demuxer で -c copy 結合
  4. 音声はクリップ音声と外部音声を 48kHz WAV で組み立て、ラウドネスを合わせてから最後に mux
  5. 想定フレーム数などを work/manifest.json に書き出す（verify.py が使う）

edit.json の書式は CLAUDE.md の「edit.json の書式」を参照。
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import info as probe_info, IMAGE_EXT  # noqa: E402

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
SR = 48000
CACHE_VERSION = 3        # 下ごしらえの処理を変えたら上げる（キャッシュを無効にする）
STD_RATES = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001),
             Fraction(30), Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60)]

COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
              "-color_range", "tv"]
X264_COMMON = "aq-mode=3:aq-strength=0.9:psy-rd=1.0,0.15:deblock=-1,-1"
ENC = {
    "master": ["-c:v", "libx264", "-preset", "veryslow", "-crf", "12", "-profile:v", "high",
               "-x264-params", X264_COMMON],
    "light": ["-c:v", "libx264", "-preset", "veryslow", "-crf", "17", "-profile:v", "high",
              "-maxrate", "24M", "-bufsize", "48M", "-x264-params", X264_COMMON],
    "draft": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"],
}
ABR = {"master": "320k", "light": "256k", "draft": "128k"}
SCALE_FLAGS = "lanczos+accurate_rnd+full_chroma_int"
TO_RGB = f"scale=in_color_matrix=bt709:in_range=tv:flags={SCALE_FLAGS},format=gbrp16le"
TO_YUV = (f"scale=out_color_matrix=bt709:out_range=tv:flags={SCALE_FLAGS},format=yuv444p10le,"
          "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")


def run(cmd, quiet=True):
    print("  $ " + " ".join(shlex.quote(c) for c in cmd)[:400], flush=True)
    r = subprocess.run(cmd, capture_output=quiet, text=True)
    if r.returncode != 0:
        if quiet:
            sys.stderr.write(r.stderr[-4000:])
        raise SystemExit(f"ffmpeg が失敗しました（終了コード {r.returncode}）")
    return r


def fesc(path):
    """フィルタ引数用のパスのエスケープ"""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def parse_fps(v):
    if isinstance(v, str) and "/" in v:
        n, d = v.split("/")
        return Fraction(int(n), int(d))
    return Fraction(v).limit_denominator(1001)


def snap_rate(r):
    """測った fps を一番近い標準のフレームレートに寄せる（3%以内）"""
    best = min(STD_RATES, key=lambda s: abs(float(s) - r))
    return best if abs(float(best) - r) / float(best) < 0.03 else None


def atempo_chain(speed):
    """atempo は 1段あたり 0.5〜100。0.5 未満は複数段に分ける"""
    parts, s = [], speed
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


# ---------------------------------------------------------------- 映像の処理

TONEMAP_METHODS = {"hable", "mobius", "reinhard", "clip"}


def tonemap_chain(method, npl):
    if method not in TONEMAP_METHODS:
        raise SystemExit(f"tonemap の方式が不明です: {method}（{', '.join(sorted(TONEMAP_METHODS))}）")
    if method == "clip":
        # トーンマップなしで BT.709 に変換する（範囲外は切り捨て）。HLG で自然に見えることがある
        return (f"zscale=t=bt709:p=bt709:m=bt709:r=tv:npl={npl}:dither=error_diffusion,"
                "format=yuv444p10le")
    return (f"zscale=t=linear:npl={npl},format=gbrpf32le,zscale=p=bt709,"
            f"tonemap={method}:desat=0,"
            f"zscale=t=bt709:m=bt709:r=tv:dither=error_diffusion,format=yuv444p10le")


def in_matrix(meta):
    cs = meta.get("colorspace", "")
    if cs in ("bt470bg", "smpte170m"):
        return "bt601"
    if cs.startswith("bt2020"):
        return "bt2020"
    if cs == "bt709":
        return "bt709"
    return "bt709" if meta.get("h", 1080) >= 720 or meta.get("w", 1920) >= 1280 else "bt601"


def reframe(src, dst, mode, W, H, fx, fy, matrix):
    """[src] → [dst]：出力サイズ W×H、yuv444p10le、BT.709 にする"""
    sc = (f"in_color_matrix={matrix}:out_color_matrix=bt709:out_range=tv:flags={SCALE_FLAGS}")
    if mode == "fit":
        return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=decrease:{sc},"
                f"format=yuv444p10le,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{dst}]")
    if mode == "blur":
        w4, h4 = W // 4, H // 4
        return (f"[{src}]split[{dst}_fg][{dst}_bg];"
                f"[{dst}_bg]scale={w4}:{h4}:force_original_aspect_ratio=increase:{sc},"
                f"format=yuv444p10le,crop={w4}:{h4},gblur=sigma=10,"
                f"scale={W}:{H}:flags=bicubic,{TO_RGB},"
                f"colorchannelmixer=rr=0.86:gg=0.86:bb=0.86,{TO_YUV}[{dst}_b];"
                f"[{dst}_fg]scale={W}:{H}:force_original_aspect_ratio=decrease:{sc},"
                f"format=yuv444p10le[{dst}_f];"
                f"[{dst}_b][{dst}_f]overlay=(W-w)/2:(H-h)/2:format=yuv444p10,setsar=1[{dst}]")
    # fill（既定）：はみ出した分を切る。fx/fy は切り出し位置（0=左/上、0.5=中央、1=右/下）
    return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase:{sc},"
            f"format=yuv444p10le,crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy},setsar=1[{dst}]")


def tone_curve(contrast, shadows, highlights):
    """コントラスト・シャドウ・ハイライトを 1 本のトーンカーブにする（各 -1〜1）"""
    p25 = 0.25 - 0.06 * contrast + 0.10 * shadows
    p75 = 0.75 + 0.06 * contrast + 0.10 * highlights
    return f"0/0 0.25/{p25:.4f} 0.5/0.5 0.75/{p75:.4f} 1/1"


def saturation_matrix(s):
    wr, wg, wb = 0.2126, 0.7152, 0.0722
    m = {}
    for o, ow in (("r", wr), ("g", wg), ("b", wb)):
        for i, iw in (("r", wr), ("g", wg), ("b", wb)):
            m[f"{o}{i}"] = iw * (1 - s) + (s if o == i else 0)
    return "colorchannelmixer=" + ":".join(f"{k}={v:.5f}" for k, v in m.items())


def grade_chain(g):
    """カラーグレーディング（gbrp16le の上で処理する）。空なら空文字"""
    if not g:
        return ""
    f = []
    if g.get("lut"):
        if not os.path.exists(g["lut"]):
            raise SystemExit(f"LUT が見つかりません: {g['lut']}")
        f.append(f"lut3d=file={fesc(g['lut'])}:interp=tetrahedral")
    if g.get("exposure"):
        f.append(f"format=gbrpf32le,exposure=exposure={float(g['exposure'])},format=gbrp16le")
    if g.get("warmth"):
        k = 6500 - 2500 * float(g["warmth"])
        f.append(f"colortemperature=temperature={k:.0f}:pl=1")
    c, sh, hi = (float(g.get(k, 0)) for k in ("contrast", "shadows", "highlights"))
    if c or sh or hi:
        f.append(f"curves=master='{tone_curve(c, sh, hi)}'")
    if g.get("curves"):
        f.append(f"curves={g['curves']}")
    if g.get("saturation", 1.0) != 1.0:
        f.append(saturation_matrix(float(g["saturation"])))
    if g.get("vibrance"):
        f.append(f"vibrance=intensity={float(g['vibrance'])}")
    if g.get("sharpen"):
        f.append(f"cas=strength={float(g['sharpen'])}")
    if g.get("deband"):
        f.append("deband=1thr=0.015:2thr=0.015:3thr=0.015:range=16:blur=1")
    return ",".join(f)


def merge_grade(project, clip):
    if clip is False or project is False:
        return {}
    g = dict(project or {})
    g.update(clip or {})
    return g


def vignette_mask(cfg, strength):
    """周辺減光用の 16bit マスク画像（中央 1.0、四隅に向かってなめらかに暗くなる）"""
    path = os.path.join(cfg.work, f"vignette_{cfg.W}x{cfg.H}_{strength:.3f}.png")
    if not os.path.exists(path):
        os.makedirs(cfg.work, exist_ok=True)
        t = "clip((hypot((X-W/2)/(W/2),(Y-H/2)/(H/2))-0.55)/(1.4142-0.55),0,1)"
        expr = f"65535*(1-{strength}*({t})*({t})*(3-2*({t})))"
        run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi", "-i",
             f"color=c=black:s={cfg.W}x{cfg.H}", "-frames:v", "1",
             "-vf", f"format=gray16le,geq=lum='{expr}'", "-pix_fmt", "gray16be", path])
    return path


def stab_params(c):
    s = c.get("stabilize")
    if not s:
        return None
    s = {} if s is True else dict(s)
    return {"shakiness": int(s.get("shakiness", 6)), "smoothing": int(s.get("smoothing", 20)),
            "zoom": float(s.get("zoom", 0))}


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
        self.tr_type, self.tr_frames, self.head = "cut", 0, 0

    def setup(self, cfg):
        F = cfg.fps
        self.frames = int(round(self.src_len / self.speed * float(F)))
        tr = self.c.get("transition") or {}
        self.tr_type = tr.get("type", "cut")
        self.tr_frames = 0 if self.tr_type == "cut" else int(round(float(tr.get("dur", 0.5)) * float(F)))
        if not self.is_image and self.meta.get("fps"):
            eff = self.meta["fps"] / self.speed      # 速度変更後に 1 秒あたり何枚の実フレームがあるか
            if eff < float(F) * 0.98 and self.c.get("interp", "none") == "none":
                print(f"  注意: クリップ{self.i} は実フレームが {eff:.1f}枚/秒しかなく、出力 {float(F):.3f}fps "
                      f"ではコマの重複でカクつきます。interp: \"motion\" か \"blend\" を検討してください")

    @property
    def tail(self):
        return self.tr_frames

    def key(self, cfg, extra=""):
        st = os.stat(self.src)
        blob = json.dumps({
            "v": CACHE_VERSION, "c": {k: v for k, v in self.c.items() if k != "transition"},
            "W": cfg.W, "H": cfg.H, "F": str(cfg.fps), "grade": cfg.grade,
            "tonemap": cfg.tonemap, "size": st.st_size, "mtime": int(st.st_mtime), "x": extra},
            sort_keys=True, default=str)
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
        if "look" in j:
            raise SystemExit("look は廃止しました。grade（カラーグレーディング）を使ってください")
        self.fps_spec = j.get("fps", "auto")
        self.fps = None
        self.grade = j.get("grade") or {}
        self.tonemap = j.get("tonemap", {"method": "hable", "npl": 100})
        self.subs = j.get("subtitles")
        self.fontsdir = j.get("fontsdir", "fonts")
        self.chunk_max = float(j.get("chunk_max", 20))
        self.clip_audio = j.get("clip_audio", True)
        self.audio = j.get("audio", [])
        self.loudness = j.get("loudness", {"i": -14, "tp": -1.0})
        self.work = os.path.join(os.path.dirname(os.path.abspath(path)), "work")
        self.out_dir = j.get("output_dir", "output")
        self.args = args

    def decide_fps(self, clips):
        if self.fps_spec != "auto":
            self.fps = parse_fps(self.fps_spec)
            return
        weight = Counter()
        for c in clips:
            if c.is_image or not c.meta.get("fps"):
                continue
            r = snap_rate(c.meta["fps"])
            if r:
                weight[r] += c.src_len
        self.fps = weight.most_common(1)[0][0] if weight else Fraction(30)
        print(f"fps: auto → {self.fps}（素材の尺で最も多いフレームレート）")


def video_graph(clip, cfg, trf=None):
    """下ごしらえのフィルタグラフ。戻り値: (入力引数, filter_complex, 出力ラベル)"""
    W, H, F = cfg.W, cfg.H, cfg.fps
    c = clip.c
    if clip.is_image:
        inp = ["-loop", "1", "-framerate", str(F), "-t", f"{clip.src_len:.6f}", "-i", clip.src]
    else:
        inp = ["-ss", f"{clip.t_in:.6f}", "-t", f"{clip.src_len:.6f}", "-i", clip.src]
    tm = dict(cfg.tonemap)
    if isinstance(c.get("tonemap"), dict):
        tm.update(c["tonemap"])
    chain = []
    matrix = in_matrix(clip.meta)
    if clip.meta.get("hdr") and c.get("tonemap", True) is not False:
        chain.append(tonemap_chain(tm.get("method", "hable"), tm.get("npl", 100)))
        matrix = "bt709"
    if trf:
        sp = stab_params(c)
        chain.append(f"format=yuv420p,vidstabtransform=input={fesc(trf)}:smoothing={sp['smoothing']}"
                     f":optzoom={1 if sp['zoom'] == 0 else 0}:zoom={sp['zoom']}:interpol=bicubic")
    if not clip.is_image:
        chain.append(f"setpts=(PTS-STARTPTS)/{clip.speed:.6f}")
        interp = c.get("interp", "none")
        if interp == "motion":
            chain.append(f"minterpolate=fps={F}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        elif interp == "blend":
            chain.append(f"framerate=fps={F}")
        chain.append(f"fps={F}")
    else:
        chain.append(f"fps={F}")
    graph = ["[0:v]" + (",".join(chain)) + "[pre]"]
    graph.append(reframe("pre", "fr", c.get("fit", "fill"), W, H,
                         c.get("focus_x", 0.5), c.get("focus_y", 0.5), matrix))
    post = []
    if c.get("vf"):
        post.append(c["vf"])
    post.append(TO_RGB)
    g = merge_grade(cfg.grade, c.get("grade"))
    gc = grade_chain(g)
    if gc:
        post.append(gc)
    extra_in = []
    vig = float(g.get("vignette", 0) or 0)
    if vig:
        mask = vignette_mask(cfg, vig)
        extra_in = ["-loop", "1", "-i", mask]
        graph.append("[fr]" + ",".join(post) + "[gr]")
        graph.append("[1:v]format=gbrp16le[mask]")
        graph.append("[gr][mask]blend=all_mode=multiply:shortest=1," + TO_YUV + "[v]")
    else:
        graph.append("[fr]" + ",".join(post) + "," + TO_YUV + "[v]")
    return inp + extra_in, ";".join(graph), "v"


def stabilize_detect(clip, cfg):
    sp = stab_params(clip.c)
    if not sp or clip.is_image:
        return None
    trf = os.path.join(cfg.work, "stab", f"clip{clip.i:02d}_{clip.key(cfg, 'trf')}.trf")
    if os.path.exists(trf):
        return trf
    os.makedirs(os.path.dirname(trf), exist_ok=True)
    pre = []
    if clip.meta.get("hdr") and clip.c.get("tonemap", True) is not False:
        tm = dict(cfg.tonemap)
        if isinstance(clip.c.get("tonemap"), dict):
            tm.update(clip.c["tonemap"])
        pre.append(tonemap_chain(tm.get("method", "hable"), tm.get("npl", 100)))
    pre.append(f"format=yuv420p,vidstabdetect=shakiness={sp['shakiness']}:accuracy=15"
               f":result={fesc(trf + '.part')}")
    print(f"[手ブレ補正] クリップ{clip.i}: 動きを解析")
    run(["ffmpeg", "-y", "-hide_banner", "-ss", f"{clip.t_in:.6f}", "-t", f"{clip.src_len:.6f}",
         "-i", clip.src, "-vf", ",".join(pre), "-f", "null", "-"])
    os.replace(trf + ".part", trf)
    return trf


def prepare_image(clip, cfg):
    """写真の EXIF 回転を反映した PNG を作る（ffmpeg は EXIF の向きを反映しないため）"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return
    if os.path.splitext(clip.src)[1].lower() == ".heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise SystemExit("HEIC を読むには pip install pillow-heif が必要です")
    im = Image.open(clip.src)
    fixed = ImageOps.exif_transpose(im)
    if fixed is im and os.path.splitext(clip.src)[1].lower() != ".heic":
        return
    out = os.path.join(cfg.work, "images", f"clip{clip.i:02d}_{clip.key(cfg, 'img')}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not os.path.exists(out):
        fixed.convert("RGB").save(out)
    clip.src = out
    clip.meta = probe_info(out) or {}


def prepare(clip, cfg):
    """クリップを出力サイズ・固定fps・10bit 4:4:4 の可逆中間ファイルにする（キャッシュあり）"""
    out = os.path.join(cfg.work, "prep", f"clip{clip.i:02d}_{clip.key(cfg)}.mkv")
    if os.path.exists(out):
        print(f"[下ごしらえ] クリップ{clip.i}: キャッシュを使用")
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    trf = stabilize_detect(clip, cfg)
    inp, fc, lab = video_graph(clip, cfg, trf)
    fc += f";[{lab}]tpad=stop_mode=clone:stop_duration=2[o]"   # 足りない場合は最終フレームで埋める
    print(f"[下ごしらえ] クリップ{clip.i}: {clip.src}  {clip.frames}フレーム")
    tmp = out + ".part.mkv"
    run(["ffmpeg", "-y", "-hide_banner", *inp, "-filter_complex", fc, "-map", "[o]",
         "-frames:v", str(clip.frames), "-c:v", "libx264", "-preset", "veryfast", "-qp", "0",
         "-pix_fmt", "yuv444p10le", *COLOR_TAGS, "-an", tmp])
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- チャンク

def sub_filter(cfg, offset_frames):
    if not cfg.subs:
        return ""
    off = offset_frames / float(cfg.fps)
    fd = f":fontsdir={fesc(cfg.fontsdir)}" if os.path.isdir(cfg.fontsdir) else ""
    return (f",setpts=PTS+{off:.6f}/TB,subtitles=filename={fesc(cfg.subs)}{fd},"
            f"setpts=PTS-STARTPTS")


def timescale(F):
    return F.numerator * (512 if F.denominator == 1 else 1)


def encode_chunk(cfg, mode, inputs, fc, nframes, out):
    W, H = cfg.W, cfg.H
    if mode == "draft":
        fc += f",scale={W // 2}:{H // 2}:flags=bicubic,format=yuv420p[v]"
    else:
        # 10bit → 8bit は誤差拡散ディザで（空などのグラデーションに段差を出さない）
        fc += ",scale=sws_dither=ed:flags=accurate_rnd+full_chroma_int,format=yuv420p[v]"
    ins = []
    for p in inputs:
        ins += ["-i", p]
    tmp = out + ".part.mp4"
    run(["ffmpeg", "-y", "-hide_banner", *ins, "-filter_complex", fc, "-map", "[v]",
         "-frames:v", str(nframes), *ENC[mode], "-pix_fmt", "yuv420p", *COLOR_TAGS,
         "-r", str(cfg.fps), "-video_track_timescale", str(timescale(cfg.fps)), "-an", tmp])
    os.replace(tmp, out)


def build_chunks(cfg, clips, preps, mode):
    """チャンク一覧を作ってエンコードし、ファイルのリストを返す"""
    cdir = os.path.join(cfg.work, f"chunks_{mode}")
    shutil.rmtree(cdir, ignore_errors=True)
    os.makedirs(cdir)
    files, pos = [], 0
    for k, clip in enumerate(clips):
        a, b = clip.head, clip.frames - clip.tail
        step = max(1, int(cfg.chunk_max * float(cfg.fps)))
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
        if clip.tail and k + 1 < len(clips):
            d = clip.tail
            out = os.path.join(cdir, f"{len(files):03d}_tr{clip.i:02d}-{clips[k + 1].i:02d}.mp4")
            fc = (f"[0:v]trim=start_frame={clip.frames - d}:end_frame={clip.frames},"
                  f"setpts=PTS-STARTPTS[a];"
                  f"[1:v]trim=start_frame=0:end_frame={d},setpts=PTS-STARTPTS[b];"
                  f"[a][b]xfade=transition={clip.tr_type}:duration={d / float(cfg.fps):.6f}:offset=0"
                  + sub_filter(cfg, pos))
            print(f"[チャンク {mode}] トランジション {clip.tr_type} {d}フレーム")
            encode_chunk(cfg, mode, [preps[k], preps[k + 1]], fc, d, out)
            files.append(out)
            pos += d
    return files, pos


# ---------------------------------------------------------------- 音声

def cleanup_chain(a):
    """ノイズ除去・低音カット（クリップ音声と外部音声で共通）"""
    f = []
    if a.get("highpass"):
        f.append(f"highpass=f={float(a['highpass'])}:poles=2")
    dn = a.get("denoise")
    if dn:
        nr = 12 if dn is True else float(dn)
        f.append(f"afftdn=nr={nr}:nf=-40:tn=1")
    if a.get("af"):
        f.append(a["af"])
    return f


def build_audio(cfg, clips, total):
    F = float(cfg.fps)
    adir = os.path.join(cfg.work, "audio")
    os.makedirs(adir, exist_ok=True)
    tracks = []
    start = 0
    for clip in clips:
        dur = clip.frames / F
        out = os.path.join(adir, f"clip{clip.i:02d}.wav")
        vol = float(clip.c.get("volume", 1.0))
        src_meta = probe_info(clip.c["src"]) or {}
        use = cfg.clip_audio and not clip.is_image and src_meta.get("has_audio") and vol > 0
        if use:
            # 注意: atempo の後ろに soxr の aresample を置くと ffmpeg 6.1 で処理が止まる（実測）。
            # 必ずリサンプル → atempo の順にする
            af = cleanup_chain(clip.c)
            af += [f"aresample={SR}:resampler=soxr:precision=28",
                   "aformat=sample_fmts=fltp:channel_layouts=stereo"]
            if abs(clip.speed - 1) > 1e-6:
                af.append(atempo_chain(clip.speed))
            af += [f"volume={vol}", "apad", f"atrim=0:{dur:.6f}", "asetpts=PTS-STARTPTS"]
            fin = clip.head / F if clip.head else 0.008
            fout = clip.tail / F if clip.tail else 0.008
            af.append(f"afade=t=in:st=0:d={fin:.6f}:curve=qsin")
            af.append(f"afade=t=out:st={dur - fout:.6f}:d={fout:.6f}:curve=qsin")
            run(["ffmpeg", "-y", "-hide_banner", "-ss", f"{clip.t_in:.6f}",
                 "-t", f"{clip.src_len:.6f}", "-i", clip.c["src"], "-vn",
                 "-af", ",".join(af), "-c:a", "pcm_f32le", out])
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
        af = cleanup_chain(a) + [
            f"aresample={SR}:resampler=soxr:precision=28",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
            f"volume={float(a.get('volume_db', 0.0))}dB", f"atrim=0:{length:.6f}"]
        if a.get("fade_in"):
            af.append(f"afade=t=in:st=0:d={float(a['fade_in'])}")
        if a.get("fade_out"):
            fo = float(a["fade_out"])
            af.append(f"afade=t=out:st={max(0.0, length - fo):.6f}:d={fo}")
        run(["ffmpeg", "-y", "-hide_banner", *inp, "-i", a["src"], "-vn",
             "-af", ",".join(af), "-c:a", "pcm_f32le", out])
        tracks.append((out, float(a.get("start", 0.0))))
    mix = os.path.join(adir, "mix.wav")
    tot = total / F
    if not tracks:
        run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi", "-i",
             f"anullsrc=r={SR}:cl=stereo", "-t", f"{tot:.6f}", "-c:a", "pcm_s24le", mix])
        return mix, None
    ins, parts = [], []
    for k, (p, st) in enumerate(tracks):
        ins += ["-i", p]
        parts.append(f"[{k}:a]adelay=delays={int(round(st * 1000))}:all=1[a{k}]")
    labels = "".join(f"[a{k}]" for k in range(len(tracks)))
    fc = (";".join(parts) + f";{labels}amix=inputs={len(tracks)}:normalize=0:dropout_transition=0,"
          f"apad,atrim=0:{tot:.6f}[out]")
    raw = os.path.join(adir, "mix_raw.wav")
    run(["ffmpeg", "-y", "-hide_banner", *ins, "-filter_complex", fc, "-map", "[out]",
         "-c:a", "pcm_f32le", raw])
    if not cfg.loudness:
        run(["ffmpeg", "-y", "-hide_banner", "-i", raw, "-c:a", "pcm_s24le", mix])
        return mix, None
    return loudnorm(raw, mix, float(cfg.loudness.get("i", -14)), float(cfg.loudness.get("tp", -1.0)))


def loudnorm(src, dst, I, TP):
    """2パスの loudnorm（linear）で統合ラウドネスを I LUFS に合わせる"""
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af",
             f"loudnorm=I={I}:TP={TP}:LRA=20:print_format=json", "-f", "null", "-"])
    m = json.loads(re.findall(r"\{[^{}]*\}", r.stderr)[-1])
    if m["input_i"] in ("-inf", "inf") or float(m["input_i"]) < -70:
        run(["ffmpeg", "-y", "-hide_banner", "-i", src, "-c:a", "pcm_s24le", dst])
        return dst, m
    af = (f"loudnorm=I={I}:TP={TP}:LRA=20:linear=true:measured_I={m['input_i']}"
          f":measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']},"
          f"aresample={SR}:resampler=soxr:precision=28")
    run(["ffmpeg", "-y", "-hide_banner", "-i", src, "-af", af, "-c:a", "pcm_s24le", dst])
    return dst, m


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


def check_fonts(cfg, total_frames):
    """テロップのフォントが本当に使われるかを libass の記録で確かめる（代替フォントへの置き換えを防ぐ）"""
    dur = total_frames / float(cfg.fps)
    fd = f":fontsdir={fesc(cfg.fontsdir)}" if os.path.isdir(cfg.fontsdir) else ""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i",
                        f"color=s=270x480:r=10:d={dur:.3f}", "-vf",
                        f"subtitles=filename={fesc(cfg.subs)}{fd}", "-f", "null", "-"],
                       capture_output=True, text=True)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    bad = []
    for want, got in set(re.findall(r"fontselect: \((.+?), \d+, \d+\) -> ([^,]+)", r.stderr)):
        if want == "Inter" and "Inter" in got:     # ASS の既定スタイル（実際には使わない）
            continue
        if norm(want) not in norm(os.path.basename(got)) and norm(os.path.basename(got)) not in norm(want):
            bad.append(f"{want} → {got}")
    if bad:
        raise SystemExit("テロップのフォントが見つからず、別のフォントに置き換わります:\n  "
                         + "\n  ".join(bad) + "\n（bash scripts/get_fonts.sh で取得するか、名前を確認してください）")


def load_clips(cfg):
    clips = [Clip(i, c, cfg) for i, c in enumerate(cfg.j["clips"])]
    cfg.decide_fps(clips)
    for c in clips:
        if c.is_image:
            prepare_image(c, cfg)
        c.setup(cfg)
    if clips:
        clips[-1].tr_frames, clips[-1].tr_type = 0, "cut"
    for k in range(1, len(clips)):
        clips[k].head = clips[k - 1].tail
    for clip in clips:
        if clip.head + clip.tail > clip.frames:
            raise SystemExit(f"クリップ{clip.i}: トランジションがクリップより長くなっています"
                             f"（{clip.head}+{clip.tail} > {clip.frames}フレーム）")
    return clips


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

    clips = load_clips(cfg)
    preps = [prepare(c, cfg) for c in clips]
    total_expected = sum(c.frames - c.tail for c in clips)
    if cfg.subs:
        check_fonts(cfg, total_expected)

    modes = ["draft"] if args.draft else (["master"] if args.no_light else ["master", "light"])
    mix, loud = build_audio(cfg, clips, total_expected)
    outputs = {}
    for mode in modes:
        files, total = build_chunks(cfg, clips, preps, mode)
        assert total == total_expected, (total, total_expected)
        suffix = {"master": "master", "light": "post", "draft": "draft"}[mode]
        out = os.path.join(cfg.out_dir, f"{cfg.name}_{suffix}.mp4")
        assemble(cfg, files, mix, mode, out)
        outputs[mode] = out

    timeline, pos = [], 0
    for c in clips:
        # pos はクリップの先頭が完成版に現れるフレーム（前とのトランジションが始まる位置）
        timeline.append({"clip": c.i, "src": c.c["src"], "start_frame": pos,
                         "frames": c.frames, "transition_out": c.tr_type,
                         "transition_frames": c.tail})
        pos += c.frames - c.tail
    manifest = {"name": cfg.name, "fps": str(cfg.fps), "w": cfg.W, "h": cfg.H,
                "total_frames": total_expected, "duration": total_expected / float(cfg.fps),
                "outputs": outputs, "timeline": timeline,
                "loudness_target": cfg.loudness, "loudness_measured_before": loud}
    with open(os.path.join(cfg.work, "manifest.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n完了: {total_expected}フレーム（{total_expected / float(cfg.fps):.3f}秒、{cfg.fps}fps）")
    for m, p in outputs.items():
        print(f"  {m}: {p}")


if __name__ == "__main__":
    main()
