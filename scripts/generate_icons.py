#!/usr/bin/env python3
"""Utility to produce various icon sizes from the main logo.

Usage:
    python scripts/generate_icons.py [output_dir]

By default it will look for ``static/logo.png`` and write to ``static/icons``.
Requires Pillow (install via ``pip install Pillow``).
"""
import sys
import os
from PIL import Image

# sizes we want to produce (w,h)
ICON_SIZES = [
    (128, 128),  # favicon
    (192, 192),  # PWA manifest
    (512, 512),  # PWA manifest
]


def main():
    cwd = os.getcwd()
    input_path = os.path.join(cwd, 'static', 'favicon.png')
    if not os.path.exists(input_path):
        print(f"error: cannot find logo at {input_path}")
        sys.exit(1)

    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(cwd, 'static', 'icons')
    os.makedirs(out_dir, exist_ok=True)

    with Image.open(input_path) as img:
        # convert to RGBA to preserve transparency if present
        img = img.convert('RGBA')
        for size in ICON_SIZES:
            fname = f"icon-{size[0]}.png"  # use width only in filename
            out_path = os.path.join(out_dir, fname)
            icon = img.copy()
            icon.thumbnail(size, Image.LANCZOS)
            # optionally we could pad to exact size if aspect ratio mismatch
            if icon.size != size:
                # create blank transparent image and paste centered
                canvas = Image.new('RGBA', size, (0, 0, 0, 0))
                x = (size[0] - icon.size[0]) // 2
                y = (size[1] - icon.size[1]) // 2
                canvas.paste(icon, (x, y), icon)
                icon = canvas
            icon.save(out_path)
            print(f"wrote {out_path}")

    print("done")


if __name__ == '__main__':
    main()
