"""Generate a placeholder UFC separator icon for scroll display.

Run this script once to create assets/sports/ufc_logos/UFC.png.
Replace with an official UFC octagon logo when available.
"""

import math
from PIL import Image, ImageDraw, ImageFont


def create_ufc_octagon_icon(output_path: str, size: int = 64):
    """Create a simple UFC octagon icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw octagon
    center_x, center_y = size // 2, size // 2
    radius = size // 2 - 2

    # Calculate octagon vertices
    points = []
    for i in range(8):
        angle = math.pi / 8 + (i * math.pi / 4)  # Start rotated for flat top
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        points.append((x, y))

    # Draw filled octagon
    draw.polygon(points, fill=(200, 16, 16, 230), outline=(255, 255, 255, 255))

    # Draw "UFC" text
    try:
        font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 10)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

    text = "UFC"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = (size - text_w) // 2
    text_y = (size - text_h) // 2

    # White text with black outline
    for dx, dy in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
        draw.text((text_x + dx, text_y + dy), text, font=font, fill=(0, 0, 0, 255))
    draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 255))

    img.save(output_path, "PNG")
    print(f"Created UFC icon at {output_path}")


if __name__ == "__main__":
    create_ufc_octagon_icon("assets/sports/ufc_logos/UFC.png")
