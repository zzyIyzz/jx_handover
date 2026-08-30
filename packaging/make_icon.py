"""Create the Windows application icon used by the onedir build."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    output = Path(__file__).with_name("handover.ico")
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(size):
        ratio = y / (size - 1)
        color = (
            int(24 + 28 * ratio),
            int(63 + 72 * ratio),
            int(108 + 78 * ratio),
            255,
        )
        for x in range(size):
            pixels[x, y] = color

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=58, fill=255)
    image.putalpha(mask)

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (25, 25, size - 26, size - 26),
        radius=43,
        outline=(255, 255, 255, 80),
        width=7,
    )
    font_path = Path("C:/Windows/Fonts/msyhbd.ttc")
    font = ImageFont.truetype(str(font_path), 124)
    text = "交"
    box = draw.textbbox((0, 0), text, font=font)
    x = (size - (box[2] - box[0])) / 2 - box[0]
    y = (size - (box[3] - box[1])) / 2 - box[1] - 4
    draw.text((x, y), text, font=font, fill="white")
    image.save(
        output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
