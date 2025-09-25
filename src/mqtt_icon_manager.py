"""
MQTT Icon Manager

This module handles icon upload, optimization, and management for MQTT displays.
It provides functionality for uploading custom icons, organizing them, and
optimizing them for LED matrix display.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageOps
from pathlib import Path
import hashlib
import time

logger = logging.getLogger(__name__)

class MQTTIconManager:
    """Manages icons for MQTT displays."""
    
    def __init__(self, base_path: str = "assets/mqtt_icons"):
        self.base_path = Path(base_path)
        self.user_uploads_path = self.base_path / "user_uploads"
        self.presets_path = self.base_path / "presets"
        self.metadata_file = self.base_path / "metadata.json"
        
        # Create directories if they don't exist
        self._create_directories()
        
        # Load metadata
        self.metadata = self._load_metadata()
        
        # Initialize preset icons
        self._initialize_preset_icons()
        
        logger.info("MQTT Icon Manager initialized")
    
    def _create_directories(self):
        """Create necessary directories."""
        self.user_uploads_path.mkdir(parents=True, exist_ok=True)
        self.presets_path.mkdir(parents=True, exist_ok=True)
    
    def _load_metadata(self) -> Dict:
        """Load icon metadata from file."""
        try:
            if self.metadata_file.exists():
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            return {"icons": {}, "categories": {}}
        except Exception as e:
            logger.error(f"Error loading icon metadata: {e}")
            return {"icons": {}, "categories": {}}
    
    def _save_metadata(self):
        """Save icon metadata to file."""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving icon metadata: {e}")
    
    def _initialize_preset_icons(self):
        """Initialize preset icons if they don't exist."""
        presets = {
            "temperature.png": "🌡️",
            "humidity.png": "💧", 
            "motion.png": "👁️",
            "door.png": "🚪",
            "light.png": "💡",
            "security.png": "🔒",
            "energy.png": "⚡",
            "water.png": "🚰",
            "fire.png": "🔥",
            "smoke.png": "💨"
        }
        
        for filename, emoji in presets.items():
            preset_path = self.presets_path / filename
            if not preset_path.exists():
                self._create_emoji_icon(emoji, preset_path)
    
    def _create_emoji_icon(self, emoji: str, output_path: Path):
        """Create an icon from an emoji."""
        try:
            # Create a 32x32 image with the emoji
            img = Image.new('RGB', (32, 32), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # Try to use a font that supports emojis
            try:
                font = ImageFont.truetype("assets/fonts/PressStart2P-Regular.ttf", 24)
            except:
                font = ImageFont.load_default()
            
            # Center the emoji
            bbox = draw.textbbox((0, 0), emoji, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (32 - text_width) // 2
            y = (32 - text_height) // 2
            
            draw.text((x, y), emoji, font=font, fill=(255, 255, 255))
            img.save(output_path, 'PNG')
            
        except Exception as e:
            logger.error(f"Error creating emoji icon: {e}")
    
    def upload_icon(self, file_data: bytes, filename: str, category: str = "custom") -> Dict:
        """Upload and process a new icon."""
        try:
            # Validate file
            if not self._validate_icon_file(file_data, filename):
                return {"success": False, "error": "Invalid file format or size"}
            
            # Generate unique filename
            file_hash = hashlib.md5(file_data).hexdigest()
            file_ext = Path(filename).suffix.lower()
            unique_filename = f"{file_hash}{file_ext}"
            
            # Save original file
            upload_path = self.user_uploads_path / unique_filename
            
            # Process and optimize the icon
            processed_data = self._process_icon(file_data)
            
            with open(upload_path, 'wb') as f:
                f.write(processed_data)
            
            # Update metadata
            icon_info = {
                "filename": unique_filename,
                "original_name": filename,
                "category": category,
                "uploaded_at": time.time(),
                "size": len(processed_data),
                "path": str(upload_path.relative_to(self.base_path))
            }
            
            self.metadata["icons"][unique_filename] = icon_info
            
            if category not in self.metadata["categories"]:
                self.metadata["categories"][category] = []
            self.metadata["categories"][category].append(unique_filename)
            
            self._save_metadata()
            
            logger.info(f"Successfully uploaded icon: {filename} -> {unique_filename}")
            
            return {
                "success": True,
                "filename": unique_filename,
                "path": icon_info["path"],
                "category": category
            }
            
        except Exception as e:
            logger.error(f"Error uploading icon: {e}")
            return {"success": False, "error": str(e)}
    
    def _validate_icon_file(self, file_data: bytes, filename: str) -> bool:
        """Validate uploaded icon file."""
        # Check file size (max 5MB)
        if len(file_data) > 5 * 1024 * 1024:
            return False
        
        # Check file extension
        valid_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp']
        file_ext = Path(filename).suffix.lower()
        if file_ext not in valid_extensions:
            return False
        
        # Try to open as image
        try:
            img = Image.open(io.BytesIO(file_data))
            img.verify()
            return True
        except:
            return False
    
    def _process_icon(self, file_data: bytes) -> bytes:
        """Process and optimize icon for LED matrix display."""
        try:
            import io
            
            # Open image
            img = Image.open(io.BytesIO(file_data))
            
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Auto-orient based on EXIF data
            img = ImageOps.exif_transpose(img)
            
            # Resize to optimal size (32x32) while maintaining aspect ratio
            img = self._resize_for_led_matrix(img)
            
            # Save as PNG with optimization
            output = io.BytesIO()
            img.save(output, format='PNG', optimize=True)
            
            return output.getvalue()
            
        except Exception as e:
            logger.error(f"Error processing icon: {e}")
            raise
    
    def _resize_for_led_matrix(self, img: Image.Image, target_size: Tuple[int, int] = (32, 32)) -> Image.Image:
        """Resize image optimally for LED matrix display."""
        # Calculate resize dimensions maintaining aspect ratio
        img.thumbnail(target_size, Image.Resampling.LANCZOS)
        
        # Create new image with target size and center the resized image
        new_img = Image.new('RGB', target_size, (0, 0, 0))
        
        # Calculate position to center the image
        x = (target_size[0] - img.size[0]) // 2
        y = (target_size[1] - img.size[1]) // 2
        
        new_img.paste(img, (x, y))
        
        return new_img
    
    def get_icons_by_category(self, category: str = None) -> List[Dict]:
        """Get icons filtered by category."""
        icons = []
        
        if category and category in self.metadata["categories"]:
            for filename in self.metadata["categories"][category]:
                if filename in self.metadata["icons"]:
                    icon_info = self.metadata["icons"][filename].copy()
                    icon_info["filename"] = filename
                    icons.append(icon_info)
        else:
            # Return all icons
            for filename, icon_info in self.metadata["icons"].items():
                icon_info = icon_info.copy()
                icon_info["filename"] = filename
                icons.append(icon_info)
        
        return sorted(icons, key=lambda x: x.get("uploaded_at", 0), reverse=True)
    
    def get_icon_path(self, filename: str) -> Optional[str]:
        """Get the full path to an icon file."""
        if filename in self.metadata["icons"]:
            icon_info = self.metadata["icons"][filename]
            if "path" in icon_info:
                full_path = self.base_path / icon_info["path"]
                if full_path.exists():
                    return str(full_path)
        
        return None
    
    def delete_icon(self, filename: str) -> bool:
        """Delete an icon and its metadata."""
        try:
            if filename not in self.metadata["icons"]:
                return False
            
            icon_info = self.metadata["icons"][filename]
            
            # Delete file
            icon_path = self.base_path / icon_info["path"]
            if icon_path.exists():
                icon_path.unlink()
            
            # Remove from metadata
            category = icon_info.get("category")
            if category and category in self.metadata["categories"]:
                if filename in self.metadata["categories"][category]:
                    self.metadata["categories"][category].remove(filename)
            
            del self.metadata["icons"][filename]
            self._save_metadata()
            
            logger.info(f"Deleted icon: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting icon: {e}")
            return False
    
    def get_categories(self) -> List[str]:
        """Get list of all icon categories."""
        return list(self.metadata["categories"].keys())
    
    def get_icon_info(self, filename: str) -> Optional[Dict]:
        """Get detailed information about an icon."""
        if filename in self.metadata["icons"]:
            icon_info = self.metadata["icons"][filename].copy()
            icon_info["filename"] = filename
            return icon_info
        return None
    
    def search_icons(self, query: str) -> List[Dict]:
        """Search icons by name or category."""
        results = []
        query_lower = query.lower()
        
        for filename, icon_info in self.metadata["icons"].items():
            if (query_lower in filename.lower() or 
                query_lower in icon_info.get("original_name", "").lower() or
                query_lower in icon_info.get("category", "").lower()):
                
                icon_info = icon_info.copy()
                icon_info["filename"] = filename
                results.append(icon_info)
        
        return sorted(results, key=lambda x: x.get("uploaded_at", 0), reverse=True)
    
    def cleanup_orphaned_files(self) -> int:
        """Clean up icon files that are no longer in metadata."""
        cleaned = 0
        
        # Check user uploads
        for file_path in self.user_uploads_path.iterdir():
            if file_path.is_file():
                filename = file_path.name
                if filename not in self.metadata["icons"]:
                    file_path.unlink()
                    cleaned += 1
                    logger.info(f"Cleaned up orphaned file: {filename}")
        
        return cleaned
