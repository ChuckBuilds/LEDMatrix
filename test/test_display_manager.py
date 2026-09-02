import os
import pytest
from unittest.mock import MagicMock, patch
from PIL import ImageDraw

# display_manager imports the hardware rgbmatrix module at import time unless
# EMULATOR=true. Use the emulator (same convention as
# test_display_dirty_tracking.py) so this file collects standalone instead of
# relying on collection order — the tests below patch RGBMatrix/
# RGBMatrixOptions explicitly, so the underlying binding doesn't matter here.
os.environ.setdefault("EMULATOR", "true")

from src.display_manager import DisplayManager

@pytest.fixture
def mock_rgb_matrix():
    """Mock the rgbmatrix library."""
    with patch('src.display_manager.RGBMatrix') as mock_matrix, \
         patch('src.display_manager.RGBMatrixOptions') as mock_options, \
         patch('src.display_manager.freetype'):
        
        # Setup matrix instance mock
        matrix_instance = MagicMock()
        matrix_instance.width = 128
        matrix_instance.height = 32
        matrix_instance.CreateFrameCanvas.return_value = MagicMock()
        matrix_instance.Clear = MagicMock()
        matrix_instance.SetImage = MagicMock()
        mock_matrix.return_value = matrix_instance
        
        yield {
            'matrix_class': mock_matrix,
            'options_class': mock_options,
            'matrix_instance': matrix_instance
        }

class TestDisplayManagerInitialization:
    """Test DisplayManager initialization."""
    
    def test_init_hardware_mode(self, test_config, mock_rgb_matrix):
        """Test initialization in hardware mode."""
        # Ensure EMULATOR env var is not set
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(test_config)
            
            assert dm.width == 128
            assert dm.height == 32
            assert dm.matrix is not None
            
            # Verify options were set correctly
            mock_rgb_matrix['options_class'].assert_called()
            options = mock_rgb_matrix['options_class'].return_value
            assert options.rows == 32
            assert options.cols == 64
            assert options.chain_length == 2
            
    def test_init_emulator_mode(self, test_config):
        """Test initialization in emulator mode."""
        # Set EMULATOR env var and patch the import
        with patch.dict('os.environ', {'EMULATOR': 'true'}), \
             patch('src.display_manager.RGBMatrix') as mock_matrix, \
             patch('src.display_manager.RGBMatrixOptions') as mock_options:
            
            # Setup matrix instance
            matrix_instance = MagicMock()
            matrix_instance.width = 128
            matrix_instance.height = 32
            mock_matrix.return_value = matrix_instance
            
            dm = DisplayManager(test_config)
            
            assert dm.width == 128
            assert dm.height == 32
            mock_matrix.assert_called()


class TestDisplayManagerDrawing:
    """Test drawing operations."""
    
    def test_clear(self, test_config, mock_rgb_matrix):
        """Test clear operation."""
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(test_config)
            dm.clear()
            # clear() calls Clear() multiple times (offscreen_canvas, current_canvas, matrix)
            assert dm.matrix.Clear.called
            
    def test_draw_text(self, test_config, mock_rgb_matrix):
        """Text drawn through draw_text must actually light pixels."""
        from PIL import Image, ImageDraw, ImageFont
        import src.display_manager as dm_mod
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager._instance = None
            dm = DisplayManager(test_config, suppress_test_pattern=True)
            # The fixture replaces the module's freetype with a MagicMock,
            # which breaks draw_text's isinstance(font, freetype.Face) check
            # (and silently swallows the draw). Give the mock a real class so
            # isinstance works and the PIL path is taken.
            dm_mod.freetype.Face = type("_FakeFace", (), {})
            # Start from a known-black canvas so the assertion below can only
            # pass if draw_text itself lit something.
            dm.image = Image.new('RGB', (dm.width, dm.height))
            dm.draw = ImageDraw.Draw(dm.image)

            dm.draw_text("Test", 0, 0, font=ImageFont.load_default())

            assert dm.image.convert("L").getbbox() is not None, \
                "draw_text lit no pixels"
            
    def test_draw_image(self, test_config, mock_rgb_matrix):
        """Test image drawing."""
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(test_config)
            
            # DisplayManager doesn't have draw_image method
            # It uses SetImage on canvas in update_display()
            # Just verify DisplayManager can handle image operations
            from PIL import Image
            test_image = Image.new('RGB', (64, 32))
            dm.image = test_image
            dm.draw = ImageDraw.Draw(dm.image)
            
            # Verify image was set
            assert dm.image is not None


class TestDisplayManagerResourceManagement:
    """Test resource management."""

    def test_cleanup(self, test_config, mock_rgb_matrix):
        """Test cleanup operation."""
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(test_config)
            dm.cleanup()

            dm.matrix.Clear.assert_called()


class TestDisplayManagerDoubleSided:
    """Double-sided mode: render once at logical size, tile across the chain."""

    def _config(self, **double_sided):
        """Build a config (physical 128x32) with the given double_sided block."""
        return {
            'display': {
                'hardware': {
                    'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1,
                    'hardware_mapping': 'adafruit-hat-pwm', 'brightness': 90,
                },
                'runtime': {'gpio_slowdown': 2},
                'double_sided': double_sided,
            },
            'timezone': 'UTC',
            'plugin_system': {'plugins_directory': 'plugins'},
        }

    def _captured_physical(self, mock_rgb_matrix):
        """Return the image handed to the canvas on the last update_display()."""
        canvas = mock_rgb_matrix['matrix_instance'].CreateFrameCanvas.return_value
        return canvas.SetImage.call_args[0][0]

    def test_horizontal_reports_logical_dimensions(self, mock_rgb_matrix):
        """Plugins see the per-screen size, not the full physical chain."""
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=True, copies=2, axis='horizontal'),
                                suppress_test_pattern=True)
            # Physical chain is 128x32; two side-by-side copies -> logical 64x32.
            assert dm.matrix.width == 64
            assert dm.matrix.height == 32
            assert (dm.width, dm.height) == (64, 32)
            assert dm.image.size == (64, 32)

    def test_horizontal_tiles_image_across_chain(self, mock_rgb_matrix):
        """The logical screen is duplicated left/right into a full-chain frame."""
        from PIL import Image
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=True, copies=2, axis='horizontal'),
                                suppress_test_pattern=True)
            logical = Image.new('RGB', (64, 32), (0, 0, 0))
            logical.putpixel((5, 5), (255, 0, 0))
            dm.image = logical
            dm.update_display()

            physical = self._captured_physical(mock_rgb_matrix)
            assert physical.size == (128, 32)
            assert physical.getpixel((5, 5)) == (255, 0, 0)
            assert physical.getpixel((69, 5)) == (255, 0, 0)  # copy shifted +64

    def test_vertical_axis_tiles_stacked(self, mock_rgb_matrix):
        """Vertical axis stacks copies (for panels on parallel outputs)."""
        from PIL import Image
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=True, copies=2, axis='vertical'),
                                suppress_test_pattern=True)
            # 128x32 split vertically -> logical 128x16.
            assert (dm.matrix.width, dm.matrix.height) == (128, 16)
            logical = Image.new('RGB', (128, 16), (0, 0, 0))
            logical.putpixel((10, 3), (0, 255, 0))
            dm.image = logical
            dm.update_display()

            physical = self._captured_physical(mock_rgb_matrix)
            assert physical.size == (128, 32)
            assert physical.getpixel((10, 3)) == (0, 255, 0)
            assert physical.getpixel((10, 19)) == (0, 255, 0)  # copy shifted +16

    def test_indivisible_dimension_disables_mode(self, mock_rgb_matrix):
        """A physical size that doesn't divide evenly falls back to single."""
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=True, copies=3, axis='horizontal'),
                                suppress_test_pattern=True)
            assert dm._double_sided is None  # 128 % 3 != 0
            assert dm.matrix.width == 128
            assert dm.image.size == (128, 32)

    def test_disabled_blits_logical_image_unchanged(self, mock_rgb_matrix):
        """With the feature off, the rendered image is sent through untouched."""
        from PIL import Image
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=False), suppress_test_pattern=True)
            assert dm._double_sided is None
            img = Image.new('RGB', (128, 32))
            dm.image = img
            dm.update_display()
            assert self._captured_physical(mock_rgb_matrix) is img

    def test_brightness_write_forwards_through_proxy(self, mock_rgb_matrix):
        """Setting brightness via the proxy reaches the real matrix."""
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(enabled=True, copies=2, axis='horizontal'),
                                suppress_test_pattern=True)
            assert dm.set_brightness(70) is True
            assert mock_rgb_matrix['matrix_instance'].brightness == 70


class TestDisplayManagerOrientation:
    """The orientation setting composes onto pixel_mapper_config for panels
    mounted upside down, without disturbing a custom pixel_mapper_config."""

    def _config(self, **hardware_overrides):
        config = {
            'display': {
                'hardware': {
                    'rows': 32, 'cols': 64, 'chain_length': 2, 'parallel': 1,
                    'hardware_mapping': 'adafruit-hat-pwm', 'brightness': 90,
                },
                'runtime': {'gpio_slowdown': 2},
            },
            'timezone': 'UTC',
            'plugin_system': {'plugins_directory': 'plugins'},
        }
        config['display']['hardware'].update(hardware_overrides)
        return config

    def test_default_orientation_leaves_pixel_mapper_config_untouched(self, mock_rgb_matrix):
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager(self._config(), suppress_test_pattern=True)
            options = mock_rgb_matrix['options_class'].return_value
            assert options.pixel_mapper_config == ''

    def test_orientation_180_appends_rotate_mapper(self, mock_rgb_matrix):
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager(self._config(orientation='180'), suppress_test_pattern=True)
            options = mock_rgb_matrix['options_class'].return_value
            assert options.pixel_mapper_config == 'Rotate:180'

    def test_orientation_180_composes_with_existing_pixel_mapper_config(self, mock_rgb_matrix):
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager(self._config(orientation='180', pixel_mapper_config='U-mapper'),
                           suppress_test_pattern=True)
            options = mock_rgb_matrix['options_class'].return_value
            assert options.pixel_mapper_config == 'U-mapper;Rotate:180'
