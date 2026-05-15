#!/usr/bin/env python3
"""
Pillow compatibility smoke test.

Exercises the Pillow APIs used throughout LEDMatrix to verify a new
Pillow version doesn't break image rendering, font handling, or resize ops.

Run after upgrading Pillow:
    python3 scripts/dev/test_pillow_compat.py
"""

import sys


def check(label, fn):
    try:
        result = fn()
        print(f"  ✓ {label}" + (f" — {result}" if result is not None else ""))
        return True
    except Exception as e:
        print(f"  ✗ {label} — {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    from PIL import Image, ImageDraw, ImageFont
    import PIL

    print(f"Pillow {PIL.__version__} on Python {sys.version.split()[0]}\n")

    failures = 0

    print("Image creation:")
    failures += not check("Image.new RGB",
        lambda: Image.new('RGB', (128, 32), (0, 0, 0)).size)
    failures += not check("Image.new RGBA",
        lambda: Image.new('RGBA', (64, 64), (255, 0, 0, 128)).size)
    failures += not check("Image.new 1-bit",
        lambda: Image.new('1', (16, 16)).size)

    print("\nDraw operations:")
    img = Image.new('RGB', (128, 32), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    failures += not check("draw.rectangle",
        lambda: draw.rectangle([0, 0, 127, 31], outline=(255, 0, 0)))
    failures += not check("draw.text",
        lambda: draw.text((2, 2), "Hello", fill=(255, 255, 255), font=font))
    failures += not check("draw.line",
        lambda: draw.line([0, 0, 127, 31], fill=(0, 255, 0)))

    print("\nFont metrics (used in text_helper, scroll_helper):")
    failures += not check("draw.textlength",
        lambda: f"{draw.textlength('Test', font=font):.1f}px")
    failures += not check("draw.textbbox",
        lambda: draw.textbbox((0, 0), "Test", font=font))

    print("\nResampling (used in logo_helper, image_utils, sports base):")
    logo = Image.new('RGBA', (200, 200), (255, 128, 0, 200))
    failures += not check("Image.Resampling.LANCZOS exists",
        lambda: str(Image.Resampling.LANCZOS))
    failures += not check("thumbnail with LANCZOS",
        lambda: (logo.thumbnail((64, 32), Image.Resampling.LANCZOS), logo.size)[1])
    big = Image.new('RGB', (300, 300), (0, 128, 255))
    failures += not check("resize with LANCZOS",
        lambda: big.resize((128, 32), Image.Resampling.LANCZOS).size)

    print("\nComposite / paste (used in display rendering):")
    base = Image.new('RGB', (128, 32), (0, 0, 0))
    overlay = Image.new('RGBA', (32, 32), (255, 0, 0, 128))
    failures += not check("paste RGBA onto RGB",
        lambda: (base.paste(overlay.convert('RGB'), (0, 0)), base.size)[1])
    failures += not check("Image.alpha_composite",
        lambda: Image.alpha_composite(
            Image.new('RGBA', (32, 32)), overlay).size)

    print("\nImage I/O:")
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    failures += not check("save/load PNG roundtrip",
        lambda: Image.open(buf).size)

    print()
    if failures == 0:
        print(f"All checks passed. Pillow {PIL.__version__} is compatible.")
        return 0
    else:
        print(f"{failures} check(s) failed — review output above.", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
