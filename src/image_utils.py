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
