# -*- coding: utf-8 -*-
"""把生成的立绘处理成桌面宠物用的 PNG。
绿幕版：立绘以纯绿 (#00FF00) 为背景生成，
- 与边缘连通的绿色区域 = 外部背景（含腿间/裙下所有缝隙）→ 抠成纯白（窗口抠底）
- 非背景的近白区域（围裙等服装）→ 填微米色，避免被抠成透明洞
- 背景掩码向内扩 1px，消除边缘绿色溢出"""
import os
from PIL import Image, ImageChops

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "avatar_raw2.jpg")
DST = os.path.join(BASE, "avatar.png")
TARGET_H = 460  # 桌面显示高度
CREAM = (248, 246, 242)  # 内部白区填充（刚好低于抠底阈值）


def main():
    import numpy as np
    from scipy import ndimage

    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    a = np.asarray(im).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]

    # 近绿掩码（含暗绿阴影：发丝间的绿缝往往偏暗）
    green = (g > 55) & (g - r > 28) & (g - b > 28)
    lab, n = ndimage.label(green)
    # 背景判定：与图像边缘连通的绿域，或面积足够大的绿域（腿间/裙侧
    # 的封闭绿缝隙不触边，但面积远大于绿眼睛等人物细节）
    edge = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    edge.discard(0)
    counts = np.bincount(lab.ravel())
    bg_ids = set(edge)
    for idx in range(1, n + 1):
        if counts[idx] >= 4000:
            bg_ids.add(idx)
    bg = np.isin(lab, list(bg_ids)) if bg_ids else np.zeros(green.shape, dtype=bool)
    # 小绿残点清理：发丝间被分隔的绿缝贴着背景（或位于下半身）→ 也算背景；
    # 眼睛等人物绿色细节在头部中央、离背景远，不受影响
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
    # 背景向人物内扩 1px：吃掉边缘绿色溢出
    bg = ndimage.binary_dilation(bg, iterations=1)
    a[bg] = (255, 255, 255)

    # 内部近白域（非背景）→ CREAM，防止被窗口抠成透明洞
    lum = (r * 299 + g * 587 + b * 114) // 1000
    inner_white = (lum >= 238) & (~bg)
    a[inner_white] = CREAM

    # 残余绿 fringe（发丝边缘零散绿像素）：直接压掉绿色调，
    # 不抠不填——保住发丝结构和眼睛高光
    stray = green & (~bg)
    if stray.any():
        capped = np.maximum(a[..., 0], a[..., 2]) + 8
        a[..., 1] = np.where(stray, np.minimum(a[..., 1], capped), a[..., 1])

    im = Image.fromarray(a.astype(np.uint8))
    white = Image.new("RGB", im.size, (255, 255, 255))

    # 裁剪内容区域
    diff = ImageChops.difference(im, white)
    bbox = diff.getbbox()
    if bbox:
        l, t, rr, bb2 = bbox
        m = 8
        im = im.crop((max(0, l - m), max(0, t - m), min(w, rr + m), min(h, bb2 + m)))

    nw = int(im.width * TARGET_H / im.height)
    im = im.resize((nw, TARGET_H), Image.LANCZOS)
    im.save(DST)
    print("avatar.png saved:", im.size)


if __name__ == "__main__":
    main()
