# -*- coding: utf-8 -*-
"""批量处理 frame_raw/*.jpg 绿幕帧 → D:\maid\avatar_<名>.png
与 prep_avatar.py 同一管线：绿幕抠白、内部近白填米色、裁剪、统一高度。"""
import os
from PIL import Image, ImageChops

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "frame_raw")
TARGET_H = 460
CREAM = (248, 246, 242)


def process(src, dst):
    import numpy as np
    from scipy import ndimage

    im = Image.open(src).convert("RGB")
    w, h = im.size
    a = np.asarray(im).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]

    green = (g > 55) & (g - r > 28) & (g - b > 28)
    lab, n = ndimage.label(green)
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    edge.discard(0)
    counts = np.bincount(lab.ravel())
    bg_ids = set(edge)
    for idx in range(1, n + 1):
        if counts[idx] >= 4000:
            bg_ids.add(idx)
    bg = np.isin(lab, list(bg_ids)) if bg_ids else np.zeros(green.shape, dtype=bool)
    objs = ndimage.find_objects(lab)
    if objs:
        bg_dil = ndimage.binary_dilation(bg, iterations=4)
        for idx in range(1, n + 1):
            if idx in bg_ids or counts[idx] < 150 or objs[idx - 1] is None:
                continue
            sl = objs[idx - 1]
            region = lab[sl] == idx
            if sl[0].start > h * 0.55 or (bg_dil[sl] & region).any():
                bg |= (lab == idx)
    bg = ndimage.binary_dilation(bg, iterations=1)
    a[bg] = (255, 255, 255)

    lum = (r * 299 + g * 587 + b * 114) // 1000
    a[(lum >= 238) & (~bg)] = CREAM

    stray = green & (~bg)
    if stray.any():
        capped = np.maximum(a[..., 0], a[..., 2]) + 8
        a[..., 1] = np.where(stray, np.minimum(a[..., 1], capped), a[..., 1])

    im = Image.fromarray(a.astype(np.uint8))
    diff = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255)))
    bbox = diff.getbbox()
    if bbox:
        l, t, rr, bb = bbox
        m = 8
        im = im.crop((max(0, l - m), max(0, t - m),
                      min(w, rr + m), min(h, bb + m)))
    nw = int(im.width * TARGET_H / im.height)
    im = im.resize((nw, TARGET_H), Image.LANCZOS)
    im.save(dst)
    print(os.path.basename(dst), im.size)


def main():
    for f in sorted(os.listdir(RAW)):
        if not f.lower().endswith((".jpg", ".png")):
            continue
        name = os.path.splitext(f)[0]
        process(os.path.join(RAW, f),
                os.path.join(BASE, "avatar_%s.png" % name))


if __name__ == "__main__":
    main()
