"""Font catalogue, upload, preview and deletion.

Routes decorate the shared `api_v3` Blueprint from ._common, so their
endpoint names are unchanged by living here.
"""
from web_interface.blueprints.api_v3 import (
    PROJECT_ROOT, Path, Response, SYSTEM_FONTS, api_v3, describe_exception,
    jsonify, logger, os, re, request, validate_file_upload,
)


@api_v3.route('/fonts/catalog', methods=['GET'])
def get_fonts_catalog():
    """Get fonts catalog"""
    try:
        # Check cache first (5 minute TTL)
        try:
            from web_interface.cache import get_cached, set_cached
            cached_result = get_cached('fonts_catalog', ttl_seconds=300)
            if cached_result is not None:
                return jsonify({'status': 'success', 'data': {'catalog': cached_result}})
        except ImportError:
            # Cache not available, continue without caching
            get_cached = None
            set_cached = None

        # Try to import freetype, but continue without it if unavailable
        try:
            import freetype
            freetype_available = True
        except ImportError:
            freetype_available = False

        # Scan assets/fonts directory for actual font files
        fonts_dir = PROJECT_ROOT / "assets" / "fonts"
        catalog = {}

        if fonts_dir.exists() and fonts_dir.is_dir():
            for filename in os.listdir(fonts_dir):
                if filename.endswith(('.ttf', '.otf', '.bdf')):
                    filepath = fonts_dir / filename
                    # Generate family name from filename (without extension)
                    family_name = os.path.splitext(filename)[0]

                    # Try to get font metadata using freetype (for TTF/OTF)
                    metadata = {}
                    if filename.endswith(('.ttf', '.otf')) and freetype_available:
                        try:
                            face = freetype.Face(str(filepath))
                            if face.valid:
                                # Get font family name from font file
                                family_name_from_font = face.family_name.decode('utf-8') if face.family_name else family_name
                                metadata = {
                                    'family': family_name_from_font,
                                    'style': face.style_name.decode('utf-8') if face.style_name else 'Regular',
                                    'num_glyphs': face.num_glyphs,
                                    'units_per_em': face.units_per_EM
                                }
                                # Use font's family name if available
                                if family_name_from_font:
                                    family_name = family_name_from_font
                        except Exception:
                            # If freetype fails, use filename-based name
                            pass

                    # Store relative path from project root
                    relative_path = str(filepath.relative_to(PROJECT_ROOT))
                    font_type = 'ttf' if filename.endswith('.ttf') else 'otf' if filename.endswith('.otf') else 'bdf'

                    # Generate human-readable display name from family_name
                    display_name = family_name.replace('-', ' ').replace('_', ' ')
                    # Add space before capital letters for camelCase names
                    display_name = re.sub(r'([a-z])([A-Z])', r'\1 \2', display_name)
                    # Add space before numbers that follow letters
                    display_name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', display_name)
                    # Clean up multiple spaces
                    display_name = ' '.join(display_name.split())

                    # Use filename (without extension) as unique key to avoid collisions
                    # when multiple files share the same family_name from font metadata
                    catalog_key = os.path.splitext(filename)[0]

                    # Check if this is a system font (cannot be deleted)
                    is_system = catalog_key.lower() in SYSTEM_FONTS

                    # BDF files are fixed-size bitmap strikes: FreeType
                    # accepts only the pixel size baked into the file. The
                    # UI needs to know that before offering a size control,
                    # or it offers a number that cannot take effect.
                    native_size = None
                    if font_type == 'bdf':
                        try:
                            from src.element_style import _read_bdf_native_size
                            native_size = _read_bdf_native_size(str(filepath))
                        except Exception as e:
                            logger.debug("Could not read native size for BDF font %s: %s",
                                         filepath, e)
                            native_size = None

                    catalog[catalog_key] = {
                        'filename': filename,
                        'family_name': family_name,
                        'display_name': display_name,
                        'path': relative_path,
                        'type': font_type,
                        'is_system': is_system,
                        'scalable': font_type != 'bdf',
                        'native_size': native_size,
                        'metadata': metadata if metadata else None
                    }

        # Cache the result (5 minute TTL) if available
        if set_cached:
            try:
                set_cached('fonts_catalog', catalog, ttl_seconds=300)
            except Exception:
                logger.error("[FontCatalog] Failed to cache fonts_catalog", exc_info=True)

        return jsonify({'status': 'success', 'data': {'catalog': catalog}})
    except Exception as e:
        logger.error("%s failed", request.path, exc_info=True)
        return jsonify({'status': 'error',
                        'message': 'An error occurred; see logs for details',
                        'details': describe_exception(e)}), 500
@api_v3.route('/fonts/tokens', methods=['GET'])
def get_font_tokens():
    """Get font size tokens"""
    try:
        # This would integrate with the actual font system
        # For now, return sample tokens
        tokens = {
            'xs': 6,
            'sm': 8,
            'md': 10,
            'lg': 12,
            'xl': 14,
            'xxl': 16
        }
        return jsonify({'status': 'success', 'data': {'tokens': tokens}})
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/fonts/upload', methods=['POST'])
def upload_font():
    """Upload font file"""
    try:
        if 'font_file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No font file provided'}), 400

        font_file = request.files['font_file']
        if font_file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400

        # Validate filename. validate_file_upload takes max_size_mb but only
        # checks the filename/extension with it -- it never looks at the
        # actual upload size, so the size limit below is enforced separately
        # before the file is saved (same pattern as the .star upload above).
        MAX_FONT_SIZE_MB = 10
        is_valid, error_msg = validate_file_upload(
            font_file.filename,
            max_size_mb=MAX_FONT_SIZE_MB,
            allowed_extensions=['.ttf', '.otf', '.bdf']
        )
        if not is_valid:
            return jsonify({'status': 'error', 'message': error_msg}), 400

        # Check file size (stated limit is MAX_FONT_SIZE_MB)
        font_file.seek(0, 2)  # Seek to end
        file_size = font_file.tell()
        font_file.seek(0)  # Reset to beginning
        max_font_size_bytes = MAX_FONT_SIZE_MB * 1024 * 1024
        if file_size > max_font_size_bytes:
            return jsonify({
                'status': 'error',
                'message': f'File too large (max {MAX_FONT_SIZE_MB}MB, got {file_size / 1024 / 1024:.1f}MB)'
            }), 400

        font_family = request.form.get('font_family', '')

        if not font_family:
            return jsonify({'status': 'error', 'message': 'Font file and family name required'}), 400

        # Validate font family name
        if not font_family.replace('_', '').replace('-', '').isalnum():
            return jsonify({'status': 'error', 'message': 'Font family name must contain only letters, numbers, underscores, and hyphens'}), 400

        # Save the font file to assets/fonts directory
        fonts_dir = PROJECT_ROOT / "assets" / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)

        # Create filename from family name
        original_ext = os.path.splitext(font_file.filename)[1].lower()
        safe_filename = f"{font_family}{original_ext}"
        filepath = fonts_dir / safe_filename

        # Check if file already exists
        if filepath.exists():
            return jsonify({'status': 'error', 'message': f'Font with name {font_family} already exists'}), 400

        # Save the file
        font_file.save(str(filepath))

        # Clear font catalog cache
        try:
            from web_interface.cache import delete_cached
            delete_cached('fonts_catalog')
        except ImportError as e:
            logger.warning("[FontUpload] Cache module not available: %s", e)
        except Exception:
            logger.error("[FontUpload] Failed to clear fonts_catalog cache", exc_info=True)

        return jsonify({
            'status': 'success',
            'message': f'Font {font_family} uploaded successfully',
            'font_family': font_family,
            'filename': safe_filename,
            'path': f'assets/fonts/{safe_filename}'
        })
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/fonts/preview', methods=['GET'])
def get_font_preview() -> tuple[Response, int] | Response:
    """Generate a preview image of text rendered with a specific font"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io
        import base64

        # Limits to prevent DoS via large image generation on constrained devices
        MAX_TEXT_CHARS = 100
        MAX_TEXT_LINES = 3
        MAX_DIM = 1024  # Max width or height in pixels
        MAX_PIXELS = 500000  # Max total pixels (e.g., ~700x700)

        font_filename = request.args.get('font', '')
        text = request.args.get('text', 'Sample Text 123')
        bg_color = request.args.get('bg', '000000')
        fg_color = request.args.get('fg', 'ffffff')

        # Validate text length and line count early
        if len(text) > MAX_TEXT_CHARS:
            return jsonify({'status': 'error', 'message': f'Text exceeds maximum length of {MAX_TEXT_CHARS} characters'}), 400
        if text.count('\n') >= MAX_TEXT_LINES:
            return jsonify({'status': 'error', 'message': f'Text exceeds maximum of {MAX_TEXT_LINES} lines'}), 400

        # Safe integer parsing for size
        try:
            size = int(request.args.get('size', 12))
        except (ValueError, TypeError, OverflowError):
            return jsonify({'status': 'error', 'message': 'Invalid font size'}), 400

        if not font_filename:
            return jsonify({'status': 'error', 'message': 'Font filename required'}), 400

        # Validate size
        if size < 4 or size > 72:
            return jsonify({'status': 'error', 'message': 'Font size must be between 4 and 72'}), 400

        # Security: Validate font_filename to prevent path traversal
        # Only allow alphanumeric, hyphen, underscore, and dot (for extension)
        safe_name = Path(font_filename).name  # Strip any directory components
        if safe_name != font_filename or '..' in font_filename:
            return jsonify({'status': 'error', 'message': 'Invalid font filename'}), 400

        # Validate extension
        allowed_extensions = ['.ttf', '.otf', '.bdf']
        has_valid_ext = any(safe_name.lower().endswith(ext) for ext in allowed_extensions)
        name_without_ext = safe_name.rsplit('.', 1)[0] if '.' in safe_name else safe_name

        # Find the font file
        fonts_dir = PROJECT_ROOT / "assets" / "fonts"
        if not fonts_dir.exists():
            return jsonify({'status': 'error', 'message': 'Fonts directory not found'}), 404

        font_path = fonts_dir / safe_name

        if not font_path.exists() and not has_valid_ext:
            # Try finding by family name (without extension)
            for ext in allowed_extensions:
                potential_path = fonts_dir / f"{name_without_ext}{ext}"
                if potential_path.exists():
                    font_path = potential_path
                    break

        # Final security check: ensure path is within fonts_dir
        try:
            font_path.resolve().relative_to(fonts_dir.resolve())
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid font path'}), 400

        if not font_path.exists():
            return jsonify({'status': 'error', 'message': f'Font file not found: {font_filename}'}), 404

        # Parse colors
        try:
            bg_rgb = tuple(int(bg_color[i:i+2], 16) for i in (0, 2, 4))
            fg_rgb = tuple(int(fg_color[i:i+2], 16) for i in (0, 2, 4))
        except (ValueError, IndexError):
            bg_rgb = (0, 0, 0)
            fg_rgb = (255, 255, 255)

        # Load font
        font = None
        if str(font_path).endswith('.bdf'):
            # BDF fonts require complex per-glyph rendering via freetype
            # Return explicit error rather than showing misleading preview with default font
            return jsonify({
                'status': 'error',
                'message': 'BDF font preview not supported. BDF fonts will render correctly on the LED matrix.'
            }), 400
        else:
            # TTF/OTF fonts
            try:
                font = ImageFont.truetype(str(font_path), size)
            except (IOError, OSError) as e:
                # IOError/OSError raised for invalid/corrupt font files
                logger.warning("[FontPreview] Failed to load font %s: %s", font_path, e)
                font = ImageFont.load_default()

        # Calculate text size
        temp_img = Image.new('RGB', (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        bbox = temp_draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Create image with padding
        padding = 10
        img_width = max(text_width + padding * 2, 100)
        img_height = max(text_height + padding * 2, 30)

        # Validate resulting image size to prevent memory/CPU spikes
        if img_width > MAX_DIM or img_height > MAX_DIM:
            return jsonify({'status': 'error', 'message': 'Requested image too large'}), 400
        if img_width * img_height > MAX_PIXELS:
            return jsonify({'status': 'error', 'message': 'Requested image too large'}), 400

        img = Image.new('RGB', (img_width, img_height), bg_rgb)
        draw = ImageDraw.Draw(img)

        # Center text
        x = (img_width - text_width) // 2
        y = (img_height - text_height) // 2

        draw.text((x, y), text, font=font, fill=fg_rgb)

        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        return jsonify({
            'status': 'success',
            'data': {
                'image': f'data:image/png;base64,{img_base64}',
                'width': img_width,
                'height': img_height
            }
        })
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
@api_v3.route('/fonts/<font_family>', methods=['DELETE'])
def delete_font(font_family: str) -> tuple[Response, int] | Response:
    """Delete a user-uploaded font file"""
    try:
        # Security: Validate font_family to prevent path traversal
        # Reject if it contains path separators or ..
        if '..' in font_family or '/' in font_family or '\\' in font_family:
            return jsonify({'status': 'error', 'message': 'Invalid font family name'}), 400

        # Only allow safe characters: alphanumeric, hyphen, underscore, dot
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', font_family):
            return jsonify({'status': 'error', 'message': 'Invalid font family name'}), 400

        # Check if this is a system font (uses module-level SYSTEM_FONTS frozenset)
        if font_family.lower() in SYSTEM_FONTS:
            return jsonify({'status': 'error', 'message': 'Cannot delete system fonts'}), 403

        # Find and delete the font file
        fonts_dir = PROJECT_ROOT / "assets" / "fonts"

        # Ensure fonts directory exists
        if not fonts_dir.exists() or not fonts_dir.is_dir():
            return jsonify({'status': 'error', 'message': 'Fonts directory not found'}), 404

        deleted = False
        deleted_filename = None

        # Only try valid font extensions (no empty string to avoid matching directories)
        for ext in ['.ttf', '.otf', '.bdf']:
            potential_path = fonts_dir / f"{font_family}{ext}"

            # Security: Verify path is within fonts_dir
            try:
                potential_path.resolve().relative_to(fonts_dir.resolve())
            except ValueError:
                continue  # Path escapes fonts_dir, skip

            if potential_path.exists() and potential_path.is_file():
                potential_path.unlink()
                deleted = True
                deleted_filename = f"{font_family}{ext}"
                break

        if not deleted:
            # Try case-insensitive match within fonts directory
            font_family_lower = font_family.lower()
            for filename in os.listdir(fonts_dir):
                # Only consider files with valid font extensions
                if not any(filename.lower().endswith(ext) for ext in ['.ttf', '.otf', '.bdf']):
                    continue

                name_without_ext = os.path.splitext(filename)[0]
                if name_without_ext.lower() == font_family_lower:
                    filepath = fonts_dir / filename

                    # Security: Verify path is within fonts_dir
                    try:
                        filepath.resolve().relative_to(fonts_dir.resolve())
                    except ValueError:
                        continue  # Path escapes fonts_dir, skip

                    if filepath.is_file():
                        filepath.unlink()
                        deleted = True
                        deleted_filename = filename
                        break

        if not deleted:
            return jsonify({'status': 'error', 'message': f'Font not found: {font_family}'}), 404

        # Clear font catalog cache
        try:
            from web_interface.cache import delete_cached
            delete_cached('fonts_catalog')
        except ImportError as e:
            logger.warning("[FontDelete] Cache module not available: %s", e)
        except Exception:
            logger.error("[FontDelete] Failed to clear fonts_catalog cache", exc_info=True)

        return jsonify({
            'status': 'success',
            'message': f'Font {deleted_filename} deleted successfully'
        })
    except Exception as e:
        logger.error('Unhandled exception', exc_info=True)
        return jsonify({'status': 'error', 'message': 'An error occurred; see logs for details', 'details': describe_exception(e)}), 500
