"""
Frame Extractor Module for Starlark Apps

Extracts individual frames from WebP animations produced by Pixlet.
Handles both static images and animated WebP files.
"""

import logging
from typing import List, Tuple, Optional
from PIL import Image

logger = logging.getLogger(__name__)


class FrameExtractor:
    """
    Extracts frames from WebP animations.

    Handles:
    - Static WebP images (single frame)
    - Animated WebP files (multiple frames with delays)
    - Frame timing and duration extraction
    """

    def __init__(self, default_frame_delay: int = 50):
        """
        Initialize frame extractor.

        Args:
            default_frame_delay: Default delay in milliseconds if not specified
        """
        self.default_frame_delay = default_frame_delay

    def load_webp(self, webp_path: str) -> Tuple[bool, Optional[List[Tuple[Image.Image, int]]], Optional[str]]:
        """
        Load WebP file and extract all frames with their delays.

        Args:
            webp_path: Path to WebP file

        Returns:
            Tuple of:
            - success: bool
            - frames: List of (PIL.Image, delay_ms) tuples, or None on failure
            - error: Error message, or None on success
        """
        try:
            with Image.open(webp_path) as img:
                # Check if animated
                is_animated = getattr(img, "is_animated", False)

                if not is_animated:
                    # Static image - single frame
                    # Convert to RGB (LED matrix needs RGB) to match animated branch format
                    logger.debug(f"Loaded static WebP: {webp_path}")
                    rgb_img = img.convert("RGB")
                    return True, [(rgb_img.copy(), self.default_frame_delay)], None

                # Animated WebP - extract all frames
                frames = []
                frame_count = getattr(img, "n_frames", 1)

                logger.debug(f"Extracting {frame_count} frames from animated WebP: {webp_path}")

                for frame_index in range(frame_count):
                    try:
                        img.seek(frame_index)

                        # Get frame duration (in milliseconds)
                        # WebP stores duration in milliseconds
                        duration = img.info.get("duration", self.default_frame_delay)

                        # Ensure minimum frame delay (prevent too-fast animations)
                        if duration < 16:  # Less than ~60fps
                            duration = 16

                        # Convert frame to RGB (LED matrix needs RGB)
                        frame = img.convert("RGB")
                        frames.append((frame.copy(), duration))

                    except EOFError:
                        logger.warning(f"Reached end of frames at index {frame_index}")
                        break
                    except Exception as e:
                        logger.warning(f"Error extracting frame {frame_index}: {e}")
                        continue

                if not frames:
                    error = "No frames extracted from WebP"
                    logger.error(error)
                    return False, None, error

                logger.debug(f"Successfully extracted {len(frames)} frames")
                return True, frames, None

        except FileNotFoundError:
            error = f"WebP file not found: {webp_path}"
            logger.error(error)
            return False, None, error
        except Exception as e:
            error = f"Error loading WebP: {e}"
            logger.error(error)
            return False, None, error

    def scale_frames(
        self,
        frames: List[Tuple[Image.Image, int]],
        target_width: int,
        target_height: int,
        method: Image.Resampling = Image.Resampling.NEAREST
    ) -> List[Tuple[Image.Image, int]]:
        """
        Scale all frames to target dimensions.

        Args:
            frames: List of (image, delay) tuples
            target_width: Target width in pixels
            target_height: Target height in pixels
            method: Resampling method (default: NEAREST for pixel-perfect scaling)

        Returns:
            List of scaled (image, delay) tuples
        """
        scaled_frames = []

        for frame, delay in frames:
            try:
                # Only scale if dimensions don't match
                if frame.width != target_width or frame.height != target_height:
                    scaled_frame = frame.resize(
                        (target_width, target_height),
                        resample=method
                    )
                    scaled_frames.append((scaled_frame, delay))
                else:
                    scaled_frames.append((frame, delay))
            except Exception as e:
                logger.warning(f"Error scaling frame: {e}")
                # Keep original frame on error
                scaled_frames.append((frame, delay))

        logger.debug(f"Scaled {len(scaled_frames)} frames to {target_width}x{target_height}")
        return scaled_frames

    def center_frames(
        self,
        frames: List[Tuple[Image.Image, int]],
        target_width: int,
        target_height: int,
        background_color: tuple = (0, 0, 0)
    ) -> List[Tuple[Image.Image, int]]:
        """
        Center frames on a larger canvas instead of scaling.
        Useful for displaying small widgets on large displays without distortion.

        Args:
            frames: List of (image, delay) tuples
            target_width: Target canvas width
            target_height: Target canvas height
            background_color: RGB tuple for background (default: black)

        Returns:
            List of centered (image, delay) tuples
        """
        centered_frames = []

        for frame, delay in frames:
            try:
                # If frame is already the right size, no centering needed
                if frame.width == target_width and frame.height == target_height:
                    centered_frames.append((frame, delay))
                    continue

                # Create black canvas at target size
                canvas = Image.new('RGB', (target_width, target_height), background_color)

                # Calculate position to center the frame
                x_offset = (target_width - frame.width) // 2
                y_offset = (target_height - frame.height) // 2

                # Paste frame onto canvas
                canvas.paste(frame, (x_offset, y_offset))
                centered_frames.append((canvas, delay))

            except Exception as e:
                logger.warning(f"Error centering frame: {e}")
                # Keep original frame on error
                centered_frames.append((frame, delay))

        logger.debug(f"Centered {len(centered_frames)} frames on {target_width}x{target_height} canvas")
        return centered_frames

    def get_total_duration(self, frames: List[Tuple[Image.Image, int]]) -> int:
        """
        Calculate total animation duration in milliseconds.

        Args:
            frames: List of (image, delay) tuples

        Returns:
            Total duration in milliseconds
        """
        return sum(delay for _, delay in frames)

    def optimize_frames(
        self,
        frames: List[Tuple[Image.Image, int]],
        max_frames: Optional[int] = None,
        target_duration: Optional[int] = None
    ) -> List[Tuple[Image.Image, int]]:
        """
        Optimize frame list by reducing frame count or adjusting timing.

        Args:
            frames: List of (image, delay) tuples
            max_frames: Maximum number of frames to keep
            target_duration: Target total duration in milliseconds

        Returns:
            Optimized list of (image, delay) tuples
        """
        if not frames:
            return frames

        optimized = frames.copy()

        # Limit frame count if specified
        if max_frames is not None and max_frames > 0 and len(optimized) > max_frames:
            # Sample frames evenly
            step = len(optimized) / max_frames
            indices = [int(i * step) for i in range(max_frames)]
            optimized = [optimized[i] for i in indices]
            logger.debug(f"Reduced frames from {len(frames)} to {len(optimized)}")

        # Adjust timing to match target duration
        if target_duration:
            current_duration = self.get_total_duration(optimized)
            if current_duration > 0:
                scale_factor = target_duration / current_duration
                optimized = [
                    (frame, max(16, int(delay * scale_factor)))
                    for frame, delay in optimized
                ]
                logger.debug(f"Adjusted timing: {current_duration}ms -> {target_duration}ms")

        return optimized

    def frames_to_gif_data(self, frames: List[Tuple[Image.Image, int]]) -> Optional[bytes]:
        """
        Convert frames to GIF byte data for caching or transmission.

        Args:
            frames: List of (image, delay) tuples

        Returns:
            GIF bytes, or None on error
        """
        if not frames:
            return None

        try:
            from io import BytesIO

            output = BytesIO()

            # Prepare frames for PIL
            images = [frame for frame, _ in frames]
            durations = [delay for _, delay in frames]

            # Save as GIF
            images[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=images[1:],
                duration=durations,
                loop=0,  # Infinite loop
                optimize=False  # Skip optimization for speed
            )

            return output.getvalue()

        except Exception as e:
            logger.error(f"Error converting frames to GIF: {e}")
            return None
