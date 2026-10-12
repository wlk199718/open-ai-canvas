# 根据 web/public/logo.svg 的三个多边形生成桌面图标（build/icon.png + build/icon.ico）。
# 用法：python scripts/make-icon.py
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024  # 先大图绘制再缩小，边缘更平滑
BG = (17, 17, 17, 255)
FG = (255, 255, 255, 255)

# logo.svg viewBox 0 0 64 64 中的多边形
SHAPES = [
    [(6, 8), (36, 8), (36, 16), (14, 16), (14, 36), (28, 36), (28, 44), (6, 44)],
    [(58, 20), (36, 20), (36, 28), (50, 28), (50, 48), (28, 48), (28, 56), (58, 56)],
    [(24, 27), (40, 27), (40, 37), (24, 37)],
]


def render() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE * 0.22, fill=BG)
    # logo 占画布 64%，居中
    scale = SIZE * 0.64 / 64
    offset = SIZE * 0.18
    for shape in SHAPES:
        draw.polygon([(offset + x * scale, offset + y * scale) for x, y in shape], fill=FG)
    return img


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "build"
    out.mkdir(parents=True, exist_ok=True)
    big = render()
    big.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
    big.resize((256, 256), Image.LANCZOS).save(
        out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print(f"icons written to {out}")


if __name__ == "__main__":
    main()
