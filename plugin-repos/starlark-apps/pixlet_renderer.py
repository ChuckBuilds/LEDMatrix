"""
Pixlet Renderer Module for Starlark Apps

Handles execution of Pixlet CLI to render .star files into WebP animations.
Supports bundled binaries and system-installed Pixlet.
"""

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class PixletRenderer:
    """
    Wrapper for Pixlet CLI rendering.

    Handles:
    - Auto-detection of bundled or system Pixlet binary
    - Rendering .star files with configuration
    - Schema extraction from .star files
    - Timeout and error handling
    """

    def __init__(self, pixlet_path: Optional[str] = None, timeout: int = 30):
        """
        Initialize the Pixlet renderer.

        Args:
            pixlet_path: Optional explicit path to Pixlet binary
            timeout: Maximum seconds to wait for rendering
        """
        self.timeout = timeout
        self.pixlet_binary = self._find_pixlet_binary(pixlet_path)

        if self.pixlet_binary:
            logger.info(f"Pixlet renderer initialized with binary: {self.pixlet_binary}")
        else:
            logger.warning("Pixlet binary not found - rendering will fail")

    def _find_pixlet_binary(self, explicit_path: Optional[str] = None) -> Optional[str]:
        """
        Find Pixlet binary using the following priority:
        1. Explicit path provided
        2. Bundled binary for current architecture
        3. System PATH

        Args:
            explicit_path: User-specified path to Pixlet

        Returns:
            Path to Pixlet binary, or None if not found
        """
        # 1. Check explicit path
        if explicit_path and os.path.isfile(explicit_path):
            if os.access(explicit_path, os.X_OK):
                logger.debug(f"Using explicit Pixlet path: {explicit_path}")
                return explicit_path
            else:
                logger.warning(f"Explicit Pixlet path not executable: {explicit_path}")

        # 2. Check bundled binary
        try:
            bundled_path = self._get_bundled_binary_path()
            if bundled_path and os.path.isfile(bundled_path):
                # Ensure executable
                if not os.access(bundled_path, os.X_OK):
                    try:
                        os.chmod(bundled_path, 0o755)
                        logger.debug(f"Made bundled binary executable: {bundled_path}")
                    except Exception as e:
                        logger.warning(f"Could not make bundled binary executable: {e}")

                if os.access(bundled_path, os.X_OK):
                    logger.debug(f"Using bundled Pixlet binary: {bundled_path}")
                    return bundled_path
        except Exception as e:
            logger.debug(f"Could not locate bundled binary: {e}")

        # 3. Check system PATH
        system_pixlet = shutil.which("pixlet")
        if system_pixlet:
            logger.debug(f"Using system Pixlet: {system_pixlet}")
            return system_pixlet

        logger.error("Pixlet binary not found in any location")
        return None

    def _get_bundled_binary_path(self) -> Optional[str]:
        """
        Get path to bundled Pixlet binary for current architecture.

        Returns:
            Path to bundled binary, or None if not found
        """
        try:
            # Determine project root (parent of plugin-repos)
            current_dir = Path(__file__).resolve().parent
            project_root = current_dir.parent.parent
            bin_dir = project_root / "bin" / "pixlet"

            # Detect architecture
            system = platform.system().lower()
            machine = platform.machine().lower()

            # Map architecture to binary name
            if system == "linux":
                if "aarch64" in machine or "arm64" in machine:
                    binary_name = "pixlet-linux-arm64"
                elif "x86_64" in machine or "amd64" in machine:
                    binary_name = "pixlet-linux-amd64"
                else:
                    logger.warning(f"Unsupported Linux architecture: {machine}")
                    return None
            elif system == "darwin":
                if "arm64" in machine:
                    binary_name = "pixlet-darwin-arm64"
                else:
                    binary_name = "pixlet-darwin-amd64"
            elif system == "windows":
                binary_name = "pixlet-windows-amd64.exe"
            else:
                logger.warning(f"Unsupported system: {system}")
                return None

            binary_path = bin_dir / binary_name
            if binary_path.exists():
                return str(binary_path)

            logger.debug(f"Bundled binary not found at: {binary_path}")
            return None

        except Exception as e:
            logger.debug(f"Error finding bundled binary: {e}")
            return None

    def is_available(self) -> bool:
        """
        Check if Pixlet is available and functional.

        Returns:
            True if Pixlet can be executed
        """
        if not self.pixlet_binary:
            return False

        try:
            result = subprocess.run(
                [self.pixlet_binary, "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"Pixlet not available: {e}")
            return False

    def get_version(self) -> Optional[str]:
        """
        Get Pixlet version string.

        Returns:
            Version string, or None if unavailable
        """
        if not self.pixlet_binary:
            return None

        try:
            result = subprocess.run(
                [self.pixlet_binary, "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.debug(f"Could not get Pixlet version: {e}")

        return None

    def render(
        self,
        star_file: str,
        output_path: str,
        config: Optional[Dict[str, Any]] = None,
        magnify: int = 1
    ) -> Tuple[bool, Optional[str]]:
        """
        Render a .star file to WebP output.

        Args:
            star_file: Path to .star file
            output_path: Where to save WebP output
            config: Configuration dictionary to pass to app
            magnify: Magnification factor (default 1)

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        if not self.pixlet_binary:
            return False, "Pixlet binary not found"

        if not os.path.isfile(star_file):
            return False, f"Star file not found: {star_file}"

        try:
            # Build command
            cmd = [
                self.pixlet_binary,
                "render",
                star_file,
                "-o", output_path,
                "-m", str(magnify)
            ]

            # Add configuration parameters
            if config:
                for key, value in config.items():
                    # Convert value to string for CLI
                    if isinstance(value, bool):
                        value_str = "true" if value else "false"
                    else:
                        value_str = str(value)
                    cmd.extend(["-c", f"{key}={value_str}"])

            logger.debug(f"Executing Pixlet: {' '.join(cmd)}")

            # Execute rendering
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=os.path.dirname(star_file)  # Run in .star file directory
            )

            if result.returncode == 0:
                if os.path.isfile(output_path):
                    logger.debug(f"Successfully rendered: {star_file} -> {output_path}")
                    return True, None
                else:
                    error = "Rendering succeeded but output file not found"
                    logger.error(error)
                    return False, error
            else:
                error = f"Pixlet failed (exit {result.returncode}): {result.stderr}"
                logger.error(error)
                return False, error

        except subprocess.TimeoutExpired:
            error = f"Rendering timeout after {self.timeout}s"
            logger.error(error)
            return False, error
        except Exception as e:
            error = f"Rendering exception: {e}"
            logger.error(error)
            return False, error

    def extract_schema(self, star_file: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Extract configuration schema from a .star file.

        Args:
            star_file: Path to .star file

        Returns:
            Tuple of (success: bool, schema: Optional[Dict], error: Optional[str])
        """
        if not self.pixlet_binary:
            return False, None, "Pixlet binary not found"

        if not os.path.isfile(star_file):
            return False, None, f"Star file not found: {star_file}"

        try:
            # Use 'pixlet info' or 'pixlet serve' to extract schema
            # Note: Schema extraction may vary by Pixlet version
            cmd = [self.pixlet_binary, "serve", star_file, "--print-schema"]

            logger.debug(f"Extracting schema: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.path.dirname(star_file)
            )

            if result.returncode == 0:
                # Parse JSON schema from output
                import json
                try:
                    schema = json.loads(result.stdout)
                    logger.debug(f"Extracted schema from: {star_file}")
                    return True, schema, None
                except json.JSONDecodeError as e:
                    error = f"Invalid schema JSON: {e}"
                    logger.warning(error)
                    return False, None, error
            else:
                # Schema extraction might not be supported
                logger.debug(f"Schema extraction not available or failed: {result.stderr}")
                return True, None, None  # Not an error, just no schema

        except subprocess.TimeoutExpired:
            error = "Schema extraction timeout"
            logger.warning(error)
            return False, None, error
        except Exception as e:
            error = f"Schema extraction exception: {e}"
            logger.warning(error)
            return False, None, error
