"""
生成应用图标 assets/icon.ico（多尺寸，供 PyInstaller 使用）。

设计（v6）：浅蓝→浅紫斜向渐变圆角底 + 中央三本书等大错位重叠
（前层白色封面印 "bk" 字样、书脊线，后两层依次露出上/左缘，构图撑满圆环）
+ 外圈双色下载进度环（实心白弧自正上方逆时针扫 2/3 圈，两端圆头与弧同轴）。
生成物：assets/icon.ico，含 256/128/64/48/32/16 六种尺寸。
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(OUT_DIR, exist_ok=True)

SIZE = 512              # 先在 512 上绘制，再缩放，保证小尺寸也清晰
BG_TOP = (168, 205, 240)      # #a8cdf0 浅蓝（左上）
BG_BOTTOM = (180, 158, 225)   # #b49ee1 浅紫（右下）
WHITE = (255, 255, 255)
INK = (165, 178, 228)         # #a5b2e4 封面 "bk" / 书脊线用浅紫蓝
RING_LIGHT = (255, 255, 255, 120)  # 进度环"未完成"段：半透明白


def rounded_rectangle_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    """带圆角的方形遮罩。"""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=255)
    return mask


def diagonal_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """左上→右下对角渐变：垂直渐变与水平渐变各混 50%。"""
    band_v = Image.new("RGB", (1, size))
    band_h = Image.new("RGB", (size, 1))
    for i in range(size):
        t = i / (size - 1)
        c = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
        band_v.putpixel((0, i), c)
        band_h.putpixel((i, 0), c)
    vgrad = band_v.resize((size, size), Image.BILINEAR)
    hgrad = band_h.resize((size, size), Image.BILINEAR)
    return Image.blend(vgrad, hgrad, 0.5)


def load_bold_font(px: int) -> ImageFont.FreeTypeFont:
    """加载无衬线粗体（bk 字样），逐级回退。"""
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()  # type: ignore[return-value]


def draw_progress_ring(img: Image.Image, s: int) -> None:
    """下载进度环：实心白弧自正上方逆时针扫 2/3 圈，两端圆头，其余为半透明白弧。"""
    cx = cy = s / 2
    r = s * 0.395
    w = s * 0.042

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    bbox = [cx - r, cy - r, cx + r, cy + r]

    # 半透明段：其余 1/3（正上方 → 右侧 → 右下）
    d.arc(bbox, start=-90, end=30, fill=RING_LIGHT, width=int(w))
    # 实心段：自正上方逆时针 240°（经左侧、下方，至右下 30°）
    # PIL 的 arc 只顺时针绘制，等价于顺时针自 30° 画到 270°
    d.arc(bbox, start=30, end=270, fill=WHITE, width=int(w))

    # 两端圆头：与弧带同轴；PIL 椭圆整像素渲染会外扩 ~1.5px（bbox 20 实测渲染 21.5），
    # 故 bbox 直径比弧宽再收 2px，实测渲染后圆头恰好完全落在弧带内
    W = int(w)
    r_mid = r - W / 2
    rr = W / 2 - 1.0
    for ang in (270, 30):
        rad = ang * math.pi / 180
        x = cx + r_mid * math.cos(rad)
        y = cy + r_mid * math.sin(rad)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=WHITE)

    img.paste(overlay, (0, 0), overlay)


def draw_closed_book(d: ImageDraw.ImageDraw, s: int) -> None:
    """中央三本书等大错位重叠：后两层依次露出上/左缘，前层白色封面 + 书脊线 + "bk"。"""
    back_tone = (205, 213, 238)   # 最后层（最深）
    mid_tone = (224, 230, 244)    # 中间层

    bw, bh = s * 0.400, s * 0.440   # 单本书尺寸（放大撑满圆环）
    gap = s * 0.016                 # 书间错位间隙（缩小，层叠更紧密）
    left0 = (s - (bw + 2 * gap)) / 2
    top0 = (s - (bh + 2 * gap)) / 2
    radius = s * 0.032

    # 三本由远及近绘制
    d.rounded_rectangle([left0, top0, left0 + bw, top0 + bh],
                        radius=radius, fill=back_tone)
    d.rounded_rectangle([left0 + gap, top0 + gap, left0 + gap + bw, top0 + gap + bh],
                        radius=radius, fill=mid_tone)

    # 前层书封面
    fl, ft = left0 + 2 * gap, top0 + 2 * gap
    d.rounded_rectangle([fl, ft, fl + bw, ft + bh], radius=radius, fill=WHITE)

    # 书脊线
    spine_x = fl + s * 0.052
    d.line([(spine_x, ft + s * 0.014), (spine_x, ft + bh - s * 0.014)],
           fill=INK, width=max(2, int(s * 0.010)))

    # 封面 "bk" 字样
    font = load_bold_font(int(s * 0.170))
    d.text(((spine_x + fl + bw) / 2 + s * 0.003, ft + bh / 2), "bk",
           font=font, fill=INK, anchor="mm")


def main() -> None:
    img = diagonal_gradient(SIZE, BG_TOP, BG_BOTTOM)

    # 进度环需要半透明，走 RGBA 覆盖层
    draw_progress_ring(img, SIZE)
    draw_closed_book(ImageDraw.Draw(img), SIZE)

    # 圆角遮罩
    mask = rounded_rectangle_mask(SIZE)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    # 轻微锐化，让小尺寸更清晰
    out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    out.save(
        ico_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"图标已生成: {ico_path}")
    print(f"  尺寸: {os.path.getsize(ico_path)} bytes")

    # 顺带存一张 PNG 便于预览
    png_path = os.path.join(OUT_DIR, "icon_preview.png")
    out.save(png_path, format="PNG")
    print(f"预览图: {png_path}")

    # 前端主界面 logo 直接引用图片文件（不再用 SVG 重画）
    logo_path = os.path.join(OUT_DIR, os.pardir, "frontend", "logo.png")
    out.resize((128, 128), Image.LANCZOS).save(logo_path, format="PNG")
    print(f"前端 logo: {logo_path}")


if __name__ == "__main__":
    main()
