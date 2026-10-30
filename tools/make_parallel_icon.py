"""Draw the Parallel logo (two parallel bars on a dark tile) and write Parallel.png / Parallel.ico."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZE = 1024
SCALE = 3  # supersampling for smooth edges

TILE = (7, 11, 22, 255)
EDGE = (22, 38, 74, 255)
BAR_MAIN = (61, 225, 255, 255)
BAR_SOFT = (36, 128, 158, 255)


def parallelogram(x, y, length, thickness, skew):
    """A slanted bar: bottom-left corner at (x, y), rising to the right by `skew`."""
    return [(x, y), (x + thickness, y), (x + thickness + skew, y - length), (x + skew, y - length)]


def render() -> Image.Image:
    s = SIZE * SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(0.22 * s)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=radius, fill=EDGE)
    inset = int(0.012 * s)
    draw.rounded_rectangle((inset, inset, s - 1 - inset, s - 1 - inset), radius=radius - inset, fill=TILE)

    thickness = int(0.15 * s)
    skew = int(0.20 * s)
    gap = int(0.10 * s)
    base_y = int(0.80 * s)
    left = int(0.20 * s)
    draw.polygon(parallelogram(left, base_y, int(0.60 * s), thickness, skew), fill=BAR_MAIN)
    draw.polygon(parallelogram(left + thickness + gap, base_y, int(0.60 * s), thickness, skew), fill=BAR_SOFT)
    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    image = render()
    image.save(ROOT / "Parallel.png")
    image.save(ROOT / "Parallel.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote Parallel.png and Parallel.ico")


if __name__ == "__main__":
    main()
