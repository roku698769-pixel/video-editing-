#!/usr/bin/env python3
"""ニュース番組風のBGM（オリジナル）を合成する。外部の素材は使わない。
使い方: synth_news_bgm.py <出力.wav> --dur 25.33 --hits 10.75,20.45 [--bpm 120]
- 0秒：導入のジングル（金管風の和音＋ティンパニ）
- 本体：16分の刻み・ベース・弦のパッド・ハイハット（Dm→B♭→F→C、1和音2小節）
- --hits の時刻：見出しの切り替えに合わせた「シュッ」と小さな打撃
- 最後：締めの和音
48kHz・32bit float のステレオWAV。
"""
import argparse
import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 48000
ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--dur", type=float, required=True)
ap.add_argument("--hits", default="")
ap.add_argument("--bpm", type=float, default=120)
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
N = int(a.dur * SR) + SR
L = np.zeros(N); R = np.zeros(N)
beat = 60 / a.bpm
bar = beat * 4


def midi(m):
    return 440 * 2 ** ((m - 69) / 12)


def saw(f, n, detune=0.0, phase=0.0):
    """帯域制限したのこぎり波（倍音を足し合わせる。折り返しノイズなし）"""
    t = np.arange(n) / SR
    out = np.zeros(n)
    f = f * (1 + detune)
    k = 1
    while k * f < 14000:
        out += np.sin(2 * np.pi * k * f * t + phase * k) / k
        k += 1
    return out * 0.55


def adsr(n, a_, d, s, r):
    e = np.full(n, s)
    na, nd, nr = int(a_ * SR), int(d * SR), int(r * SR)
    na = max(na, 1)
    e[:na] = np.linspace(0, 1, na)
    e[na:na + nd] = np.linspace(1, s, len(e[na:na + nd]))
    if nr > 0 and nr < n:
        e[-nr:] *= np.linspace(1, 0, nr) ** 2
    return e


def lp(x, fc, q=0.707):
    sos = signal.butter(2, min(fc, SR / 2 - 100) / (SR / 2), "low", output="sos")
    return signal.sosfilt(sos, x)


def hp(x, fc):
    sos = signal.butter(2, fc / (SR / 2), "high", output="sos")
    return signal.sosfilt(sos, x)


def add(x, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    if i >= N:
        return
    x = x[: N - i]
    gl = np.cos((pan + 1) * np.pi / 4) * np.sqrt(2)
    gr = np.sin((pan + 1) * np.pi / 4) * np.sqrt(2)
    L[i:i + len(x)] += x * gain * gl
    R[i:i + len(x)] += x * gain * gr


def tvf(x, f0, f1, tau):
    """時間とともに閉じるローパス（ブロックごとに係数を変える）"""
    out = np.zeros_like(x)
    blk = 256
    zi = np.zeros((1, 2))
    for s in range(0, len(x), blk):
        t = s / SR
        fc = f1 + (f0 - f1) * np.exp(-t / tau)
        sos = signal.butter(2, min(fc, SR / 2 - 100) / (SR / 2), "low", output="sos")
        out[s:s + blk], zi = signal.sosfilt(sos, x[s:s + blk], zi=zi)
    return out


# ---- 音色
def brass(notes, dur, t0, gain):
    n = int((dur + 0.6) * SR)
    for i, m in enumerate(notes):
        for dt, pan in ((-0.004, -0.5), (0.004, 0.5)):
            x = saw(midi(m), n, dt, rng.uniform(0, 6))
            x = tvf(x, 5200, 900, 0.25) * adsr(n, 0.012, 0.35, 0.45, 0.5)
            add(x, t0, gain / len(notes), pan * (0.3 + 0.1 * i))


def timp(t0, m, gain):
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    f = midi(m) * (1 + 0.08 * np.exp(-t / 0.03))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = (np.sin(ph) + 0.35 * np.sin(1.5 * ph) + 0.2 * np.sin(2 * ph)) * np.exp(-t / 0.55)
    nz = lp(rng.standard_normal(n), 900) * np.exp(-t / 0.03) * 0.6
    add((x + nz) * 0.6, t0, gain)


def pluck(t0, m, gain, pan):
    n = int(0.22 * SR)
    x = saw(midi(m), n, 0, rng.uniform(0, 6))
    x = tvf(x, 3800, 500, 0.05) * adsr(n, 0.003, 0.08, 0.15, 0.08)
    add(x, t0, gain, pan)


def bass(t0, m, d, gain):
    n = int(d * SR)
    x = saw(midi(m), n) * 0.8 + np.sin(2 * np.pi * midi(m) * np.arange(n) / SR) * 0.6
    x = tvf(x, 1400, 220, 0.06) * adsr(n, 0.004, 0.1, 0.6, 0.04)
    add(x, t0, gain, 0)


def pad(t0, notes, d, gain):
    n = int((d + 0.4) * SR)
    for i, m in enumerate(notes):
        for dt, pan in ((-0.006, -0.7), (0.0, 0.0), (0.006, 0.7)):
            x = saw(midi(m), n, dt, rng.uniform(0, 6))
            x = lp(x, 1800) * adsr(n, 0.35, 0.2, 0.85, 0.45)
            # 弦らしいゆれ
            x *= 1 + 0.04 * np.sin(2 * np.pi * 5.2 * np.arange(n) / SR + rng.uniform(0, 6))
            add(x, t0, gain / (len(notes) * 3), pan)


def hat(t0, gain, open_=False):
    n = int((0.18 if open_ else 0.05) * SR)
    t = np.arange(n) / SR
    x = hp(rng.standard_normal(n), 7500) * np.exp(-t / (0.06 if open_ else 0.012))
    add(x, t0, gain, 0.25)


def whoosh(t0, d, gain):
    n = int(d * SR)
    x = rng.standard_normal(n)
    out = np.zeros(n)
    blk = 256
    zi = np.zeros((1, 2))
    for s in range(0, n, blk):
        p = s / n
        fc = 400 * (12000 / 400) ** (np.sin(np.pi * p * 0.5) ** 2)
        lo, hi = fc * 0.7, min(fc * 1.4, SR / 2 - 200)
        sos = signal.butter(1, [lo / (SR / 2), hi / (SR / 2)], "band", output="sos")
        out[s:s + blk], zi = signal.sosfilt(sos, x[s:s + blk], zi=zi)
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 2
    env *= np.linspace(0.6, 1, n)
    out *= env
    i = int(t0 * SR)
    pan = np.linspace(-0.6, 0.6, n)
    gl = np.cos((pan + 1) * np.pi / 4) * np.sqrt(2)
    gr = np.sin((pan + 1) * np.pi / 4) * np.sqrt(2)
    m = min(n, N - i)
    L[i:i + m] += (out * gl * gain)[:m]
    R[i:i + m] += (out * gr * gain)[:m]


# ---- 構成
D, Bb, F_, C = 62, 58, 65, 60  # 根音（中音域）
prog = [  # (ベースの根音, パッドの和音)
    (38, [62, 65, 69, 74]),      # Dm
    (34, [58, 62, 65, 69]),      # B♭maj7
    (41, [60, 65, 69, 72]),      # F/C 的な響き
    (36, [60, 64, 67, 72]),      # C
]
hits = [float(x) for x in a.hits.split(",") if x]
end_hit = a.dur - 1.15

# 導入のジングル
brass([50, 57, 62, 65, 69, 74], 1.2, 0.06, 0.55)
timp(0.06, 38, 0.9)
timp(0.06 + beat * 1.5, 38, 0.5)
whoosh(0.0, 0.35, 0.10)

start = 0.06 + beat * 2  # 本体の開始
t = start
k = 0
while t < end_hit - 0.01:
    root, chord = prog[(k // 2) % len(prog)]
    bar_end = min(t + bar, end_hit)
    if k % 2 == 0:
        pad(t, chord, min(bar * 2, end_hit - t), 0.30)
    # 16分の刻み（根音と5度・オクターブ）
    pattern = [0, 12, 7, 12, 0, 12, 7, 15, 0, 12, 7, 12, 0, 19, 12, 7]
    for s16 in range(16):
        tt = t + s16 * beat / 4
        if tt >= bar_end - 0.01:
            break
        acc = 1.0 if s16 % 4 == 0 else 0.62
        pluck(tt, root + 24 + pattern[s16], 0.13 * acc, -0.35 if s16 % 2 else 0.35)
    # ベース：8分
    for e8 in range(8):
        tt = t + e8 * beat / 2
        if tt >= bar_end - 0.01:
            break
        bass(tt, root, beat / 2 * 0.85, 0.30 if e8 % 2 == 0 else 0.22)
    # ハイハット
    for e8 in range(8):
        tt = t + e8 * beat / 2
        if tt >= bar_end - 0.01:
            break
        hat(tt, 0.05 if e8 % 2 else 0.03, open_=(e8 == 7))
    # 小節頭のティンパニ（2小節に1回）
    if k % 2 == 0:
        timp(t, root, 0.45)
    t += bar
    k += 1

# 見出しの切り替え
for h in hits:
    whoosh(h - 0.28, 0.5, 0.16)
    timp(h + 0.2, 38, 0.55)
    brass([62, 69, 74], 0.25, h + 0.2, 0.22)

# 締め
brass([50, 57, 62, 65, 69, 74], 1.1, end_hit, 0.6)
timp(end_hit, 38, 1.0)

# ---- 残響（ステレオの合成残響）
ir_n = int(1.6 * SR)
ti = np.arange(ir_n) / SR
irl = rng.standard_normal(ir_n) * np.exp(-ti / 0.35)
irr = rng.standard_normal(ir_n) * np.exp(-ti / 0.35)
irl = lp(irl, 6000); irr = lp(irr, 6000)
pre = int(0.018 * SR)
irl = np.concatenate([np.zeros(pre), irl]); irr = np.concatenate([np.zeros(pre), irr])
irl /= np.sqrt(np.sum(irl ** 2)); irr /= np.sqrt(np.sum(irr ** 2))
wl = signal.fftconvolve(L, irl)[:N]; wr = signal.fftconvolve(R, irr)[:N]
L2 = L + 0.22 * wl; R2 = R + 0.22 * wr
# 低域の濁りを取る
L2 = hp(L2, 35); R2 = hp(R2, 35)
out = np.stack([L2, R2], 1)[: int(a.dur * SR)]
# 末尾のフェード
fo = int(0.25 * SR)
out[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 2
out = np.tanh(out / np.max(np.abs(out)) * 1.2) / np.tanh(1.2) * 0.89
wavfile.write(a.out, SR, out.astype(np.float32))
print("書き出し", a.out, f"{len(out) / SR:.2f}s")
