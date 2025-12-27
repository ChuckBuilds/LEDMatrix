"""
LEDMatrix Common CLI

Command-line interface for LEDMatrix Common utilities.
"""

import argparse
import sys
from pathlib import Path


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LEDMatrix Common Utilities",
        prog="ledmatrix-common"
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Test command
    test_parser = subparsers.add_parser('test', help='Test common utilities')
    test_parser.add_argument('--display-width', type=int, default=128, help='Display width')
    test_parser.add_argument('--display-height', type=int, default=64, help='Display height')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate configuration')
    validate_parser.add_argument('config_file', help='Configuration file to validate')
    
    args = parser.parse_args()
    
    if args.command == 'test':
        test_utilities(args.display_width, args.display_height)
    elif args.command == 'validate':
        validate_config(args.config_file)
    else:
        parser.print_help()


def test_utilities(display_width: int, display_height: int):
    """Test common utilities."""
    print(f"Testing LEDMatrix Common utilities with {display_width}x{display_height} display")
    
    try:
        from ledmatrix_common import LogoHelper, TextHelper, APIHelper, DisplayHelper, GameHelper, ConfigHelper
        
        # Test LogoHelper
        print("Testing LogoHelper...")
        logo_helper = LogoHelper(display_width, display_height)
        print(f"Logo cache stats: {logo_helper.get_cache_stats()}")
        
        # Test TextHelper
        print("Testing TextHelper...")
        text_helper = TextHelper()
        fonts = text_helper.load_fonts()
        print(f"Loaded {len(fonts)} fonts")
        
        # Test DisplayHelper
        print("Testing DisplayHelper...")
        display_helper = DisplayHelper(display_width, display_height)
        img = display_helper.create_base_image()
        print(f"Created {img.size} base image")
        
        # Test GameHelper
        print("Testing GameHelper...")
        game_helper = GameHelper()
        print("GameHelper initialized")
        
        # Test ConfigHelper
        print("Testing ConfigHelper...")
        config_helper = ConfigHelper()
        print("ConfigHelper initialized")
        
        print("All tests passed!")
        
    except ImportError as e:
        print(f"Import error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Test error: {e}")
        sys.exit(1)


def validate_config(config_file: str):
    """Validate configuration file."""
    config_path = Path(config_file)
    
    if not config_path.exists():
        print(f"Configuration file not found: {config_file}")
        sys.exit(1)
    
    try:
        from ledmatrix_common import ConfigHelper
        
        config_helper = ConfigHelper()
        config = config_helper.load_config(config_path)
        
        if config:
            print(f"Configuration loaded successfully from {config_file}")
            print(f"Found {len(config)} top-level keys")
        else:
            print(f"Failed to load configuration from {config_file}")
            sys.exit(1)
            
    except Exception as e:
        print(f"Validation error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
