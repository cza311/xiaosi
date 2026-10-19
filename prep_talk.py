# -*- coding: utf-8 -*-
"""从 avatar_front 生成说话口型帧（avatar_talka / avatar_talkb）。
嘴部定位：二次元立绘脸部=图上部最大皮肤色连通域，嘴≈该区域底部中央。
形变：嘴部圆域纵向拉伸+下移模拟张嘴，帧B幅度更大并轻微加深（口腔阴影）。"""
import os
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "avatar.png")


def gen(im, face, drop, stretch, darken):
    """对嘴部圆域做形变：drop=下移像素，stretch=纵向拉伸，darken=下半圆压暗。
    只改动形变区域本身，不做全图模糊（避免整图抖动）。"""
    a = np.asarray(im).astype(np.float32)
    h, w = a.shape[:2]
    l, t, r, b = face
    fw, fh = r - l, b - t
    cx, cy = (l + r) // 2, b - int(fh * 0.10)   # 嘴心：脸域底部中央略上
    rad = max(6, int(fw * 0.13))                  # 嘴域半径
    y0, y1 = max(0, cy - rad), min(h, cy + rad + 4)
    x0, x1 = max(0, cx - rad), min(w, cx + rad)
    region = a[y0:y1, x0:x1].copy()
    # 纵向拉伸（以圆域上缘为锚）
    rh = region.shape[0]
    new_h = int(rh * (1 + stretch))
    yi = (np.arange(new_h) * rh / new_h).clip(0, rh - 1).astype(int)
    stretched = region[yi]
    # 下移 drop 像素贴回（顶部用第一行填充）
    out = np.zeros_like(stretched)
    if drop > 0:
        out[drop:] = stretched[:new_h - drop]
        out[:drop] = stretched[0]
    else:
        out = stretched
    # 下半圆轻微压暗（张嘴阴影）
    if darken > 0:
        hh = out.shape[0]
        shade = (np.arange(hh) / hh)[:, None, None] * darken
        out = out * (1 - shade)
    # 形变区域局部平滑 1px，弱化贴回边缘的锯齿
    out_img = Image.fromarray(out.clip(0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.6))
    a[y0:y0 + out.shape[0], x0:x1] = np.asarray(out_img, dtype=np.float32)
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def main():
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    # 嘴部位置（对当前 avatar.png 实测：脸在中线上部，唇线 y≈0.105h）
    cx, cy = int(w * 0.50), int(h * 0.105)
    rad = max(5, int(w * 0.045))  # 嘴域半径（188 宽图约 8px）
    face = (cx - rad * 2, cy - rad * 3, cx + rad * 2, cy + rad * 2)
    print("mouth at (%d,%d) rad=%d size=%s" % (cx, cy, rad, (w, h)))

    gen(im, face, drop=2, stretch=0.03, darken=0.07).save(
        os.path.join(BASE, "avatar_talka.png"))
    gen(im, face, drop=4, stretch=0.06, darken=0.14).save(
        os.path.join(BASE, "avatar_talkb.png"))
    print("talk frames saved")


if __name__ == "__main__":
    main()
