"""生成 App Store 用的 1024x1024 不透明图标（无需任何外部素材）。

用法: python xcode/scripts/make_icon.py
输出: xcode/Resources/Assets.xcassets/AppIcon.appiconset/AppIcon.png
颜色与工程皮肤一致，白色播放三角 + 逐帧边框，呼应「米帧盒」。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

OUT = (Path(__file__).resolve().parent.parent
       / "Resources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon.png")
S = 1024

# 深蓝紫 → 品紫 竖向渐变
img = Image.new("RGB", (S, S))
px = img.load()
top, bottom = (14, 20, 48), (110, 52, 200)
for y in range(S):
    t = y / (S - 1)
    c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    for x in range(S):
        px[x, y] = c

# 柔和光晕
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)
g.ellipse((S * 0.18, S * 0.05, S * 0.92, S * 0.72), fill=(80, 220, 210, 46))
glow = glow.filter(ImageFilter.GaussianBlur(120))
img = Image.alpha_composite(img.convert("RGBA"), glow)
d = ImageDraw.Draw(img)

# 四角「帧」标记
m, L, w = 118, 96, 16
for cx, cy, sx, sy in ((m, m, 1, 1), (S - m, m, -1, 1),
                       (m, S - m, 1, -1), (S - m, S - m, -1, -1)):
    d.line((cx, cy, cx + sx * L, cy), fill=(255, 255, 255, 210), width=w)
    d.line((cx, cy, cx, cy + sy * L), fill=(255, 255, 255, 210), width=w)

# 外框 + 播放三角
r = 250
cx, cy = S // 2, S // 2 + 10
d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 235), width=22)
tri = [(cx - 78, cy - 132), (cx - 78, cy + 132), (cx + 138, cy)]
d.polygon(tri, fill=(255, 255, 255, 245))

# 底部三个帧点
for i, off in enumerate((-52, 0, 52)):
    d.ellipse((cx + off - 12, S - 178, cx + off + 12, S - 154),
              fill=(255, 255, 255, 220 if i == 1 else 120))

OUT.parent.mkdir(parents=True, exist_ok=True)
img.convert("RGB").save(OUT, "PNG", optimize=True)
print(f"icon -> {OUT}  {OUT.stat().st_size} bytes  {img.size}")
