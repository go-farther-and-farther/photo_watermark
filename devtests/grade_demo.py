# -*- coding: utf-8 -*-
"""调色模板样张：把 6 套模板套在同一张照片上，排出 2x3 对照图"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, r'D:\Software files\photo_watermark')
import color_grade as cg

ROOT = Path(r'D:\Software files\photo_watermark')
SRC = ROOT / 'input' / 'FUJIFILM.jpg'
OUT = ROOT / 'output' / 'grade_templates_demo.jpg'
ORDER = ['原图', '标准', '视频感', '清新', '暖调', '黑白']


def label(img, text):
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 30)
    d.rectangle([0, 0, img.width, 46], fill=(0, 0, 0))
    d.text((12, 8), text, font=f, fill=(255, 255, 255))
    return img


def main():
    im = Image.open(SRC).convert('RGB')
    im.thumbnail((900, 900), Image.LANCZOS)
    panels = []
    for name in ORDER:
        if name == cg.OFF_TEMPLATE:
            panel = im.copy()
        else:
            panel = cg.apply_grade(im, cg.BUILTIN_TEMPLATES[name], 1.0)
        panels.append(label(panel, name))
    w, h = panels[0].size
    sheet = Image.new('RGB', (w * 3 + 16, h * 2 + 8), (255, 255, 255))
    for i, p in enumerate(panels):
        sheet.paste(p, ((i % 3) * (w + 8), (i // 3) * (h + 8)))
    sheet.save(OUT, quality=90)
    print('wrote', OUT, sheet.size)


if __name__ == '__main__':
    main()
