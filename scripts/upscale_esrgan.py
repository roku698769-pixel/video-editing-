#!/usr/bin/env python3
"""Real-ESRGAN による高解像度化（CPU、torch）。
使い方: upscale_esrgan.py <入力の画像フォルダ> <出力フォルダ> --model <.pth> [--arch rrdb2|vgg4] [--dni 0.5 --wdn <wdn.pth>] [--tile 0]
入力は PNG 連番（ffmpeg で書き出したもの）。出力も PNG（16bit にはしない。モデルは 8bit 前提）。
モデル: RealESRGAN_x2plus（rrdb2）、realesr-general-x4v3（vgg4。wdn と混ぜてノイズ除去の強さを調整）
"""
import argparse, os, glob, sys, time
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F
from PIL import Image

class RDB(nn.Module):
    def __init__(s, f=64, g=32):
        super().__init__()
        s.conv1 = nn.Conv2d(f, g, 3, 1, 1); s.conv2 = nn.Conv2d(f+g, g, 3, 1, 1)
        s.conv3 = nn.Conv2d(f+2*g, g, 3, 1, 1); s.conv4 = nn.Conv2d(f+3*g, g, 3, 1, 1)
        s.conv5 = nn.Conv2d(f+4*g, f, 3, 1, 1); s.l = nn.LeakyReLU(0.2, True)
    def forward(s, x):
        x1 = s.l(s.conv1(x)); x2 = s.l(s.conv2(torch.cat((x, x1), 1)))
        x3 = s.l(s.conv3(torch.cat((x, x1, x2), 1))); x4 = s.l(s.conv4(torch.cat((x, x1, x2, x3), 1)))
        return s.conv5(torch.cat((x, x1, x2, x3, x4), 1)) * 0.2 + x
class RRDB(nn.Module):
    def __init__(s, f=64):
        super().__init__(); s.rdb1 = RDB(f); s.rdb2 = RDB(f); s.rdb3 = RDB(f)
    def forward(s, x): return s.rdb3(s.rdb2(s.rdb1(x))) * 0.2 + x
class RRDBNet(nn.Module):
    def __init__(s, scale=2, f=64, nb=23):
        super().__init__(); s.scale = scale
        s.conv_first = nn.Conv2d(3 * (4 if scale == 2 else 1), f, 3, 1, 1)
        s.body = nn.Sequential(*[RRDB(f) for _ in range(nb)]); s.conv_body = nn.Conv2d(f, f, 3, 1, 1)
        s.conv_up1 = nn.Conv2d(f, f, 3, 1, 1); s.conv_up2 = nn.Conv2d(f, f, 3, 1, 1)
        s.conv_hr = nn.Conv2d(f, f, 3, 1, 1); s.conv_last = nn.Conv2d(f, 3, 3, 1, 1); s.l = nn.LeakyReLU(0.2, True)
    def forward(s, x):
        if s.scale == 2: x = F.pixel_unshuffle(x, 2)
        fe = s.conv_first(x); fe = fe + s.conv_body(s.body(fe))
        fe = s.l(s.conv_up1(F.interpolate(fe, scale_factor=2, mode='nearest')))
        fe = s.l(s.conv_up2(F.interpolate(fe, scale_factor=2, mode='nearest')))
        return s.conv_last(s.l(s.conv_hr(fe)))
class SRVGG(nn.Module):
    def __init__(s, f=64, nc=32, up=4):
        super().__init__(); s.up = up
        b = [nn.Conv2d(3, f, 3, 1, 1), nn.PReLU(num_parameters=f)]
        for _ in range(nc): b += [nn.Conv2d(f, f, 3, 1, 1), nn.PReLU(num_parameters=f)]
        b += [nn.Conv2d(f, 3 * up * up, 3, 1, 1)]; s.body = nn.ModuleList(b)
    def forward(s, x):
        o = x
        for m in s.body: o = m(o)
        return F.pixel_shuffle(o, s.up) + F.interpolate(x, scale_factor=s.up, mode='nearest')

def load(path):
    d = torch.load(path, map_location='cpu', weights_only=True)
    return d.get('params_ema', d.get('params', d))

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--model', required=True)
ap.add_argument('--arch', default='rrdb2'); ap.add_argument('--wdn'); ap.add_argument('--dni', type=float, default=1.0)
ap.add_argument('--bf16', action='store_true', help='bf16 で計算（AMX のある CPU で速い）')
ap.add_argument('--only', help='カンマ区切りのファイル名だけ処理')
a = ap.parse_args()
torch.set_num_threads(os.cpu_count())
net = RRDBNet(2) if a.arch == 'rrdb2' else SRVGG()
sd = load(a.model)
if a.wdn:  # ノイズ除去版と重みを混ぜる（dni=1 で元のモデルのみ）
    sw = load(a.wdn); sd = {k: a.dni * sd[k] + (1 - a.dni) * sw[k] for k in sd}
net.load_state_dict(sd, strict=True); net.eval()
os.makedirs(a.dst, exist_ok=True)
files = sorted(glob.glob(os.path.join(a.src, '*.png')))
if a.only: files = [f for f in files if os.path.basename(f) in a.only.split(',')]
t0 = time.time()
for i, f in enumerate(files):
    out = os.path.join(a.dst, os.path.basename(f))
    if os.path.exists(out): continue
    im = np.asarray(Image.open(f).convert('RGB')).astype(np.float32) / 255
    x = torch.from_numpy(im).permute(2, 0, 1)[None]
    with torch.inference_mode(), torch.autocast('cpu', dtype=torch.bfloat16, enabled=a.bf16):
        y = net(x).float().clamp(0, 1)[0].permute(1, 2, 0).numpy()
    Image.fromarray((y * 255 + 0.5).astype(np.uint8)).save(out + '.tmp.png', compress_level=1)
    os.replace(out + '.tmp.png', out)
    if i % 20 == 0: print(f'{i+1}/{len(files)} {time.time()-t0:.0f}s', flush=True)
print('done', len(files), f'{time.time()-t0:.0f}s')
