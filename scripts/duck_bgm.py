#!/usr/bin/env python3
"""ナレーションが鳴っている間だけ BGM を下げる（ダッキング）。
使い方: duck_bgm.py <bgm.wav> <出力.wav> --base-db -20 --duck-db -8 --key <音声.wav>@<開始秒> [--key ...]
- base-db：BGM 全体の音量
- duck-db：ナレーション中にさらに下げる量
- 声の有無は 20ms ごとの RMS で判定し、下げ始め 0.12 秒・戻り 0.45 秒でなめらかにつなぐ（声の直前から下げ始める）
"""
import argparse
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import maximum_filter1d

ap = argparse.ArgumentParser()
ap.add_argument("bgm"); ap.add_argument("out")
ap.add_argument("--base-db", type=float, default=-20)
ap.add_argument("--duck-db", type=float, default=-8)
ap.add_argument("--key", action="append", default=[])
a = ap.parse_args()

sr, x = wavfile.read(a.bgm)
x = x.astype(np.float64)
if x.ndim == 1:
    x = x[:, None]
n = len(x)
hop = int(sr * 0.02)
nb = n // hop + 1
active = np.zeros(nb, bool)
for k in a.key:
    path, st = k.rsplit("@", 1)
    ksr, v = wavfile.read(path)
    assert ksr == sr, "サンプリング周波数をそろえてください"
    v = v.astype(np.float64)
    if v.ndim > 1:
        v = v.mean(1)
    off = int(float(st) * sr)
    m = len(v) // hop
    rms = np.sqrt(np.array([np.mean(v[i * hop:(i + 1) * hop] ** 2) for i in range(m)]) + 1e-12)
    db = 20 * np.log10(rms / (np.max(rms) + 1e-12))
    on = db > -38
    # 語と語の短い切れ目（0.6 秒未満）では戻さない
    on = maximum_filter1d(on.astype(float), size=31) > 0
    i0 = off // hop
    seg = active[i0:i0 + m]
    seg |= on[:len(seg)]
# 下げ始めを声の 0.12 秒前に、戻りを 0.45 秒かけて
g_db = np.where(active, a.duck_db, 0.0)
lead = int(0.12 / 0.02)
g_db = np.minimum(g_db, np.concatenate([g_db[lead:], np.zeros(lead)]))
out_db = np.zeros_like(g_db)
cur = 0.0
att, rel = 1 - np.exp(-1 / (0.12 / 0.02)), 1 - np.exp(-1 / (0.45 / 0.02))
for i, tgt in enumerate(g_db):
    cur += (tgt - cur) * (att if tgt < cur else rel)
    out_db[i] = cur
gain = 10 ** ((out_db + a.base_db) / 20)
gs = np.interp(np.arange(n), np.arange(nb) * hop, gain)
y = x * gs[:, None]
wavfile.write(a.out, sr, y.astype(np.float32))
print("書き出し", a.out, f"ダッキングの区間 {active.mean() * 100:.0f}%")
