import os
import pytest
from unittest.mock import MagicMock, patch

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

    @pytest.mark.parametrize('orientation', ['90', '270'])
    def test_sideways_orientation_appends_rotate_mapper(self, mock_rgb_matrix, orientation):
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager(self._config(orientation=orientation), suppress_test_pattern=True)
            options = mock_rgb_matrix['options_class'].return_value
            assert options.pixel_mapper_config == f'Rotate:{orientation}'

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


class TestDisplayManagerLibraryGuard:
    """For many settings it can't use the library returns no matrix (which the
    binding doesn't check, so the process crashes on its next call) or calls
    abort() -- on every board, with more on a Pi 5. DisplayManager has to refuse
    before creating the matrix and fall back, reporting why."""

    def _config(self, **hardware_overrides):
        config = {
            'display': {
                'hardware': {
                    'rows': 48, 'cols': 96, 'chain_length': 1, 'parallel': 1,
                    'hardware_mapping': 'regular', 'brightness': 90,
                },
                'runtime': {'gpio_slowdown': 2},
            },
            'timezone': 'UTC',
            'plugin_system': {'plugins_directory': 'plugins'},
        }
        config['display']['hardware'].update(hardware_overrides)
        return config

    @pytest.fixture
    def board(self, tmp_path, monkeypatch):
        from src import pi5_matrix_support

        def set_model(model):
            path = tmp_path / 'model'
            path.write_bytes(model.encode() + b'\x00')
            monkeypatch.setattr(pi5_matrix_support, 'MODEL_PATH', str(path))
        return set_model

    def test_unsupported_setting_on_pi5_never_creates_the_matrix(self, mock_rgb_matrix, board):
        board('Raspberry Pi 5 Model B Rev 1.0')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(row_address_type=5), suppress_test_pattern=True)
        mock_rgb_matrix['matrix_class'].assert_not_called()
        assert dm.matrix is None

    def test_supported_setting_on_pi5_creates_the_matrix(self, mock_rgb_matrix, board):
        board('Raspberry Pi 5 Model B Rev 1.0')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(row_address_type=2), suppress_test_pattern=True)
        mock_rgb_matrix['matrix_class'].assert_called_once()
        assert dm.matrix is not None

    def test_pi5_only_limits_do_not_apply_to_other_boards(self, mock_rgb_matrix, board):
        board('Raspberry Pi 4 Model B Rev 1.5')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(row_address_type=5), suppress_test_pattern=True)
        mock_rgb_matrix['matrix_class'].assert_called_once()
        assert dm.matrix is not None

    @pytest.fixture
    def hw_status(self, tmp_path, monkeypatch):
        """What DisplayManager writes to /tmp/led_matrix_hw_status.json."""
        import json as _json
        import tempfile as _tempfile
        from src import display_manager as dm_module
        written = {}
        real_replace = os.replace
        real_mkstemp = _tempfile.mkstemp

        def fake_mkstemp(dir=None, prefix=None):
            return real_mkstemp(dir=str(tmp_path), prefix=prefix)

        def fake_replace(src, dst):
            if str(dst).endswith('led_matrix_hw_status.json'):
                with open(src) as f:
                    written.update(_json.load(f))
                os.remove(src)
            else:
                real_replace(src, dst)

        monkeypatch.setattr(dm_module.tempfile, 'mkstemp', fake_mkstemp)
        monkeypatch.setattr(dm_module.os, 'replace', fake_replace)
        monkeypatch.setattr(dm_module.os.path, 'islink', lambda _p: False)
        return written

    @pytest.mark.parametrize('overrides,named', [
        ({'hardware_mapping': 'adafruit-hat-pwm', 'parallel': 2}, 'parallel 2'),
        ({'hardware_mapping': 'adafruit-hat', 'parallel': 3}, 'parallel 3'),
        ({'hardware_mapping': 'adafruit-hat-pwn'}, 'adafruit-hat-pwn'),
        ({'rows': 128}, 'rows 128'),
        ({'chain_length': 300}, 'chain_length 300'),
    ])
    def test_hand_edited_setting_the_library_refuses_falls_back_on_any_board(
            self, mock_rgb_matrix, board, hw_status, overrides, named):
        board('Raspberry Pi 4 Model B Rev 1.5')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(**overrides), suppress_test_pattern=True)
        mock_rgb_matrix['matrix_class'].assert_not_called()
        assert dm.matrix is None
        assert hw_status['ok'] is False
        assert hw_status['cause'] == 'settings'
        assert named in hw_status['error']

    def test_library_failure_is_reported_as_the_library(self, mock_rgb_matrix, board, hw_status):
        board('Raspberry Pi 4 Model B Rev 1.5')
        mock_rgb_matrix['matrix_class'].side_effect = RuntimeError('boom')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            dm = DisplayManager(self._config(), suppress_test_pattern=True)
        assert dm.matrix is None
        assert hw_status == {'ok': False, 'error': 'boom', 'cause': 'library'}

    def test_success_reports_no_cause(self, mock_rgb_matrix, board, hw_status):
        board('Raspberry Pi 4 Model B Rev 1.5')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'false'}):
            DisplayManager(self._config(), suppress_test_pattern=True)
        assert hw_status == {'ok': True, 'error': None, 'cause': None}

    def test_emulator_warns_but_still_starts(self, mock_rgb_matrix, board, caplog):
        board('Raspberry Pi 4 Model B Rev 1.5')
        DisplayManager._instance = None
        with patch.dict('os.environ', {'EMULATOR': 'true'}):
            dm = DisplayManager(self._config(rows=128), suppress_test_pattern=True)
        mock_rgb_matrix['matrix_class'].assert_called_once()
        assert dm.matrix is not None
        assert 'rows 128' in caplog.text

    def test_refused_settings_advice_does_not_send_users_to_rebuild(self, board):
        """The Pi 5 rebuild / GPIO slowdown hint used to follow every failure,
        including settings LEDMatrix itself refused."""
        from src.matrix_support import MatrixSettingsRefused
        board('Raspberry Pi 5 Model B Rev 1.0')
        advice = DisplayManager._fallback_advice(
            'settings', MatrixSettingsRefused('row address type 5'))
        assert 'Display tab' in advice
        assert 'first_time_install' not in advice and 'slowdown' not in advice
        library = DisplayManager._fallback_advice('library', RuntimeError('mmap failed'))
        assert 'RPI_RGB_FORCE_REBUILD=1' in library
        board('Raspberry Pi 4 Model B Rev 1.5')
        assert 'RPI_RGB_FORCE_REBUILD' not in DisplayManager._fallback_advice(
            'library', RuntimeError('boom'))
