"""生成安卓开屏页 presplash：白底居中大图 App 图标（buildozer.spec presplash.filename 引用）。

p4a 开屏以 fitCenter 居中显示本图，四周露出的底色由 android.presplash_color
（#FFFFFF）填充——白底无缝，任何分辨率下图标都居中。
用法（任意目录）: python assets/make_presplash.py
"""

import os

from PIL import Image

W, H = 1600, 2560  # 与原 presplash 一致，竖屏手机 fitCenter 无裁切
ICON_SRC = "icon_preview.png"  # App 图标（512x512 RGBA，与桌面/启动器同源）
ICON_SIZE = 600  # 图标边长（px）：画布宽度 37.5%，1080p 竖屏上约占屏宽五成
ICON_CY = 0.44  # 图标中心高度（视觉中心略高于几何中心）


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    img = Image.new("RGB", (W, H), "#FFFFFF")
    icon = Image.open(os.path.join(here, ICON_SRC)).convert("RGBA")
    icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    cx, cy = W // 2, int(H * ICON_CY)
    img.paste(icon, (cx - ICON_SIZE // 2, cy - ICON_SIZE // 2), icon)

    out = os.path.join(here, "presplash.png")
    img.save(out)
    print(f"presplash.png 已生成: {out} ({W}x{H}), icon={ICON_SRC}@{ICON_SIZE}px")


if __name__ == "__main__":
    main()
