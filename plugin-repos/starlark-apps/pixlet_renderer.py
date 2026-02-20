"""
Pixlet Renderer Module for Starlark Apps

Handles execution of Pixlet CLI to render .star files into WebP animations.
Supports bundled binaries and system-installed Pixlet.
"""

import json
import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

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
            logger.info(f"[Starlark Pixlet] Pixlet renderer initialized with binary: {self.pixlet_binary}")
        else:
            logger.warning("[Starlark Pixlet] Pixlet binary not found - rendering will fail")

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
                    except OSError:
                        logger.exception(f"Could not make bundled binary executable: {bundled_path}")

                if os.access(bundled_path, os.X_OK):
                    logger.debug(f"Using bundled Pixlet binary: {bundled_path}")
                    return bundled_path
        except OSError:
            logger.exception("Could not locate bundled binary")

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

        except OSError:
            logger.exception("Error finding bundled binary")
            return None

    def _get_safe_working_directory(self, star_file: str) -> Optional[str]:
        """
        Get a safe working directory for subprocess execution.

        Args:
            star_file: Path to .star file

        Returns:
            Resolved parent directory, or None if empty or invalid
        """
        try:
            resolved_parent = os.path.dirname(os.path.abspath(star_file))
            # Return None if empty string to avoid FileNotFoundError
            if not resolved_parent:
                logger.debug(f"Empty parent directory for star_file: {star_file}")
                return None
            return resolved_parent
        except (OSError, ValueError):
            logger.debug(f"Could not resolve working directory for: {star_file}")
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
        except subprocess.TimeoutExpired:
            logger.debug("Pixlet version check timed out")
            return False
        except (subprocess.SubprocessError, OSError):
            logger.exception("Pixlet not available")
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
        except subprocess.TimeoutExpired:
            logger.debug("Pixlet version check timed out")
        except (subprocess.SubprocessError, OSError):
            logger.exception("Could not get Pixlet version")

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
            # Build command - config params must be POSITIONAL between star_file and flags
            # Format: pixlet render <file.star> [key=value]... [flags]
            cmd = [
                self.pixlet_binary,
                "render",
                star_file
            ]

            # Add configuration parameters as positional arguments (BEFORE flags)
            if config:
                for key, value in config.items():
                    # Validate key format (alphanumeric + underscore only)
                    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
                        logger.warning(f"Skipping invalid config key: {key}")
                        continue

                    # Convert value to string for CLI
                    if isinstance(value, bool):
                        value_str = "true" if value else "false"
                    elif isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
                        # JSON string - keep as-is, will be properly quoted by subprocess
                        value_str = value
                    else:
                        value_str = str(value)

                    # Validate value doesn't contain dangerous shell metacharacters
                    # Block: backticks, $(), pipes, redirects, semicolons, ampersands, null bytes
                    # Allow: most printable chars including spaces, quotes, brackets, braces
                    if re.search(r'[`$|<>&;\x00]|\$\(', value_str):
                        logger.warning(f"Skipping config value with unsafe shell characters for key {key}: {value_str}")
                        continue

                    # Add as positional argument (not -c flag)
                    cmd.append(f"{key}={value_str}")

            # Add flags AFTER positional config arguments
            cmd.extend([
                "-o", output_path,
                "-m", str(magnify)
            ])

            # Build sanitized command for logging (redact sensitive values)
            sanitized_cmd = [self.pixlet_binary, "render", star_file]
            if config:
                config_keys = list(config.keys())
                sanitized_cmd.append(f"[{len(config_keys)} config entries: {', '.join(config_keys)}]")
            sanitized_cmd.extend(["-o", output_path, "-m", str(magnify)])
            logger.debug(f"Executing Pixlet: {' '.join(sanitized_cmd)}")

            # Execute rendering
            safe_cwd = self._get_safe_working_directory(star_file)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=safe_cwd  # Run in .star file directory (or None if relative path)
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
        except (subprocess.SubprocessError, OSError):
            logger.exception("Rendering exception")
            return False, "Rendering failed - see logs for details"

    def extract_schema(self, star_file: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Extract configuration schema from a .star file by parsing source code.

        Supports:
        - Static field definitions (location, text, toggle, dropdown, color, datetime)
        - Variable-referenced dropdown options
        - Graceful degradation for unsupported field types

        Args:
            star_file: Path to .star file

        Returns:
            Tuple of (success: bool, schema: Optional[Dict], error: Optional[str])
        """
        if not os.path.isfile(star_file):
            return False, None, f"Star file not found: {star_file}"

        try:
            # Read .star file
            with open(star_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Parse schema from source
            schema = self._parse_schema_from_source(content, star_file)

            if schema:
                field_count = len(schema.get('schema', []))
                logger.debug(f"Extracted schema with {field_count} field(s) from: {star_file}")
                return True, schema, None
            else:
                # No schema found - not an error, app just doesn't have configuration
                logger.debug(f"No schema found in: {star_file}")
                return True, None, None

        except UnicodeDecodeError as e:
            error = f"File encoding error: {e}"
            logger.warning(error)
            return False, None, error
        except Exception as e:
            logger.exception(f"Schema extraction failed for {star_file}")
            return False, None, f"Schema extraction error: {str(e)}"

    def _parse_schema_from_source(self, content: str, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Parse get_schema() function from Starlark source code.

        Args:
            content: .star file content
            file_path: Path to file (for logging)

        Returns:
            Schema dict with format {"version": "1", "schema": [...]}, or None
        """
        # Extract variable definitions (for dropdown options)
        var_table = self._extract_variable_definitions(content)

        # Extract get_schema() function body
        schema_body = self._extract_get_schema_body(content)
        if not schema_body:
            logger.debug(f"No get_schema() function found in {file_path}")
            return None

        # Extract version
        version_match = re.search(r'version\s*=\s*"([^"]+)"', schema_body)
        version = version_match.group(1) if version_match else "1"

        # Extract fields array from schema.Schema(...) - handle nested brackets
        fields_start_match = re.search(r'fields\s*=\s*\[', schema_body)
        if not fields_start_match:
            # Empty schema or no fields
            return {"version": version, "schema": []}

        # Find matching closing bracket
        bracket_count = 1
        i = fields_start_match.end()
        while i < len(schema_body) and bracket_count > 0:
            if schema_body[i] == '[':
                bracket_count += 1
            elif schema_body[i] == ']':
                bracket_count -= 1
            i += 1

        if bracket_count != 0:
            # Unmatched brackets
            logger.warning(f"Unmatched brackets in schema fields for {file_path}")
            return {"version": version, "schema": []}

        fields_text = schema_body[fields_start_match.end():i-1]

        # Parse individual fields
        schema_fields = []
        # Match schema.FieldType(...) patterns
        field_pattern = r'schema\.(\w+)\s*\((.*?)\)'

        # Find all field definitions (handle nested parentheses)
        pos = 0
        while pos < len(fields_text):
            match = re.search(field_pattern, fields_text[pos:], re.DOTALL)
            if not match:
                break

            field_type = match.group(1)
            field_start = pos + match.start()
            field_end = pos + match.end()

            # Handle nested parentheses properly
            paren_count = 1
            i = pos + match.start() + len(f'schema.{field_type}(')
            while i < len(fields_text) and paren_count > 0:
                if fields_text[i] == '(':
                    paren_count += 1
                elif fields_text[i] == ')':
                    paren_count -= 1
                i += 1

            field_params_text = fields_text[pos + match.start() + len(f'schema.{field_type}('):i-1]

            # Parse field
            field_dict = self._parse_schema_field(field_type, field_params_text, var_table)
            if field_dict:
                schema_fields.append(field_dict)

            pos = i

        return {
            "version": version,
            "schema": schema_fields
        }

    def _extract_variable_definitions(self, content: str) -> Dict[str, List[Dict]]:
        """
        Extract top-level variable assignments (for dropdown options).

        Args:
            content: .star file content

        Returns:
            Dict mapping variable names to their option lists
        """
        var_table = {}

        # Find variable definitions like: variableName = [schema.Option(...), ...]
        var_pattern = r'^(\w+)\s*=\s*\[(.*?schema\.Option.*?)\]'
        matches = re.finditer(var_pattern, content, re.MULTILINE | re.DOTALL)

        for match in matches:
            var_name = match.group(1)
            options_text = match.group(2)

            # Parse schema.Option entries
            options = self._parse_schema_options(options_text, {})
            if options:
                var_table[var_name] = options

        return var_table

    def _extract_get_schema_body(self, content: str) -> Optional[str]:
        """
        Extract get_schema() function body using indentation-aware parsing.

        Args:
            content: .star file content

        Returns:
            Function body text, or None if not found
        """
        # Find def get_schema(): line
        pattern = r'^(\s*)def\s+get_schema\s*\(\s*\)\s*:'
        match = re.search(pattern, content, re.MULTILINE)

        if not match:
            return None

        # Get the indentation level of the function definition
        func_indent = len(match.group(1))
        func_start = match.end()

        # Split content into lines starting after the function definition
        lines_after = content[func_start:].split('\n')
        body_lines = []

        for line in lines_after:
            # Skip empty lines
            if not line.strip():
                body_lines.append(line)
                continue

            # Calculate indentation of current line
            stripped = line.lstrip()
            line_indent = len(line) - len(stripped)

            # If line has same or less indentation than function def, check if it's a top-level def
            if line_indent <= func_indent:
                # This is a line at the same or outer level - check if it's a function
                if re.match(r'def\s+\w+', stripped):
                    # Found next top-level function, stop here
                    break
                # Otherwise it might be a comment or other top-level code, stop anyway
                break

            # Line is indented more than function def, so it's part of the body
            body_lines.append(line)

        if body_lines:
            return '\n'.join(body_lines)
        return None

    def _parse_schema_field(self, field_type: str, params_text: str, var_table: Dict) -> Optional[Dict[str, Any]]:
        """
        Parse individual schema field definition.

        Args:
            field_type: Field type (Location, Text, Toggle, etc.)
            params_text: Field parameters text
            var_table: Variable lookup table

        Returns:
            Field dict, or None if parse fails
        """
        # Map Pixlet field types to JSON typeOf
        type_mapping = {
            'Location': 'location',
            'Text': 'text',
            'Toggle': 'toggle',
            'Dropdown': 'dropdown',
            'Color': 'color',
            'DateTime': 'datetime',
            'OAuth2': 'oauth2',
            'PhotoSelect': 'photo_select',
            'LocationBased': 'location_based',
            'Typeahead': 'typeahead',
            'Generated': 'generated',
        }

        type_of = type_mapping.get(field_type, field_type.lower())

        # Skip Generated fields (invisible meta-fields)
        if type_of == 'generated':
            return None

        field_dict = {"typeOf": type_of}

        # Extract common parameters
        # id
        id_match = re.search(r'id\s*=\s*"([^"]+)"', params_text)
        if id_match:
            field_dict['id'] = id_match.group(1)
        else:
            # id is required, skip field if missing
            return None

        # name
        name_match = re.search(r'name\s*=\s*"([^"]+)"', params_text)
        if name_match:
            field_dict['name'] = name_match.group(1)

        # desc
        desc_match = re.search(r'desc\s*=\s*"([^"]+)"', params_text)
        if desc_match:
            field_dict['desc'] = desc_match.group(1)

        # icon
        icon_match = re.search(r'icon\s*=\s*"([^"]+)"', params_text)
        if icon_match:
            field_dict['icon'] = icon_match.group(1)

        # default (can be string, bool, or variable reference)
        # First try to match quoted strings (which may contain commas)
        default_match = re.search(r'default\s*=\s*"([^"]*)"', params_text)
        if not default_match:
            # Try single quotes
            default_match = re.search(r"default\s*=\s*'([^']*)'", params_text)
        if not default_match:
            # Fall back to unquoted value (stop at comma or closing paren)
            default_match = re.search(r'default\s*=\s*([^,\)]+)', params_text)

        if default_match:
            default_value = default_match.group(1).strip()
            # Handle boolean
            if default_value in ('True', 'False'):
                field_dict['default'] = default_value.lower()
            # Handle string literal from first two patterns (already extracted without quotes)
            elif re.search(r'default\s*=\s*["\']', params_text):
                # This was a quoted string, use the captured content directly
                field_dict['default'] = default_value
            # Handle variable reference (can't resolve, use as-is)
            else:
                # Try to extract just the value if it's like options[0].value
                if '.' in default_value or '[' in default_value:
                    # Complex expression, skip default
                    pass
                else:
                    field_dict['default'] = default_value

        # For dropdown, extract options
        if type_of == 'dropdown':
            options_match = re.search(r'options\s*=\s*([^,\)]+)', params_text)
            if options_match:
                options_ref = options_match.group(1).strip()
                # Check if it's a variable reference
                if options_ref in var_table:
                    field_dict['options'] = var_table[options_ref]
                # Or inline options
                elif options_ref.startswith('['):
                    # Find the full options array (handle nested brackets)
                    # This is tricky, for now try to extract inline options
                    inline_match = re.search(r'options\s*=\s*(\[.*?\])', params_text, re.DOTALL)
                    if inline_match:
                        options_text = inline_match.group(1)
                        field_dict['options'] = self._parse_schema_options(options_text, var_table)

        return field_dict

    def _parse_schema_options(self, options_text: str, var_table: Dict) -> List[Dict[str, str]]:
        """
        Parse schema.Option list.

        Args:
            options_text: Text containing schema.Option(...) entries
            var_table: Variable lookup table (not currently used)

        Returns:
            List of {"display": "...", "value": "..."} dicts
        """
        options = []

        # Match schema.Option(display = "...", value = "...")
        option_pattern = r'schema\.Option\s*\(\s*display\s*=\s*"([^"]+)"\s*,\s*value\s*=\s*"([^"]+)"\s*\)'
        matches = re.finditer(option_pattern, options_text)

        for match in matches:
            options.append({
                "display": match.group(1),
                "value": match.group(2)
            })

        return options
