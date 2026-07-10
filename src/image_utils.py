"""Deprecated: use src/adaptive_images.py (fit_image) instead.

This module predates the adaptive image system and has no known callers.
It is kept only so any out-of-tree code importing it keeps working.
"""

import logging
from PIL import Image

logger = logging.getLogger(__name__)

def scale_to_max_dimensions(img, max_width, max_height):
    h_to_w_ratio = img.height / img.width
    w_to_h_ratio = img.width / img.height

    if img.height > max_height:
        img = img.resize((int(max_height * w_to_h_ratio), max_height), Image.Resampling.LANCZOS)
    
    if img.width > max_width:
        img = img.resize((max_width, int(max_width * h_to_w_ratio)), Image.Resampling.LANCZOS)
    
    return img
