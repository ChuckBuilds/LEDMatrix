#!/usr/bin/env python3
"""
Demonstration of dynamic font defaults system.
Shows how plugins can register their own font defaults instead of relying on hardcoded values.
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from font_manager import FontManager

def demonstrate_dynamic_defaults():
    """Demonstrate the dynamic font defaults system."""

    print("Dynamic Font Defaults Demonstration")
    print("=" * 50)

    # Create a minimal config for testing
    config = {
        "fonts": {
            "families": {
                "press_start": "assets/fonts/PressStart2P-Regular.ttf",
                "four_by_six": "assets/fonts/4x6-font.ttf"
            },
            "tokens": {
                "xs": 6, "sm": 8, "md": 10, "lg": 12, "xl": 14
            }
        }
    }

    # Initialize font manager
    font_manager = FontManager(config)
    print("Font manager initialized")

    # Set up the font manager for plugin defaults (normally done by DisplayManager)
    font_manager._plugin_defaults = {}

    # Show initial baseline defaults (before plugin registration)
    print(f"\nInitial baseline defaults count: {len(font_manager._generate_dynamic_baseline_defaults())}")

    # Simulate a plugin registering its font defaults
    plugin_defaults = {
        "custom_plugin.live.score": {"family": "press_start", "size_token": "lg"},
        "custom_plugin.live.time": {"family": "four_by_six", "size_token": "xs"},
        "custom_plugin.live.team": {"family": "press_start", "size_token": "md"},
        "custom_plugin.weather.temp": {"family": "press_start", "size_token": "sm"},
        "custom_plugin.weather.condition": {"family": "four_by_six", "size_token": "xs"}
    }

    print(f"\nRegistering plugin font defaults for 'custom_plugin'...")
    font_manager.register_plugin_font_defaults("custom_plugin", plugin_defaults)

    # Show updated baseline defaults (after plugin registration)
    updated_defaults = font_manager._generate_dynamic_baseline_defaults()
    print(f"Updated baseline defaults count: {len(updated_defaults)}")

    # Show the specific plugin defaults that were registered
    plugin_keys = [key for key in updated_defaults.keys() if "custom_plugin" in key]
    print(f"Plugin-specific defaults registered: {len(plugin_keys)}")
    for key in sorted(plugin_keys):
        print(f"   - {key}: {updated_defaults[key]}")

    # Test that the defaults work
    print("\nTesting font default resolution...")
    test_cases = [
        ("custom_plugin.live.score", "Should use plugin default"),
        ("nfl.live.score", "Should use existing default"),
        ("nonexistent.sport.score", "Should return None (no default)")
    ]

    for element_key, description in test_cases:
        default = font_manager._get_smart_defaults(element_key)
        if default:
            print(f"   + {element_key}: {default} ({description})")
        else:
            print(f"   - {element_key}: No default ({description})")

    # Demonstrate unregistering plugin defaults
    print("\nUnregistering plugin font defaults...")
    font_manager.unregister_plugin_font_defaults("custom_plugin")

    # Show final baseline defaults (after plugin unregistration)
    final_defaults = font_manager._generate_dynamic_baseline_defaults()
    print(f"Final baseline defaults count: {len(final_defaults)}")

    final_plugin_keys = [key for key in final_defaults.keys() if "custom_plugin" in key]
    print(f"Plugin-specific defaults remaining: {len(final_plugin_keys)}")

    print("\nDynamic font defaults system working correctly!")
def demonstrate_plugin_manifest_structure():
    """Show the proper plugin manifest structure for font defaults."""

    print("\nPlugin Manifest Structure for Font Defaults")
    print("=" * 50)

    sample_manifest = {
        "name": "Custom Sports Plugin",
        "version": "1.0.0",
        "description": "A plugin that displays custom sports information",
        "entry_point": "manager.py",
        "class_name": "CustomSportsPlugin",
        "fonts": {
            "fonts": [
                {
                    "family": "sports_display",
                    "source": "fonts/sports_display.ttf",
                    "description": "Custom font for sports displays"
                }
            ]
        },
        "font_defaults": {
            "custom_sports.live.score": {
                "family": "sports_display",
                "size_token": "xl"
            },
            "custom_sports.live.time": {
                "family": "sports_display",
                "size_token": "sm"
            },
            "custom_sports.live.team": {
                "family": "sports_display",
                "size_token": "md"
            }
        }
    }

    print("Plugin manifest should include a 'font_defaults' section:")
    print(json.dumps(sample_manifest["font_defaults"], indent=2))

    print("\nBenefits of Dynamic Font Defaults:")
    print("   * No hardcoded dependencies on specific managers")
    print("   * Plugins can define their own element keys")
    print("   * Automatic cleanup when plugins are unloaded")
    print("   * Future-proof for new plugin types")
    print("   * Better maintainability and flexibility")

if __name__ == "__main__":
    demonstrate_dynamic_defaults()
    demonstrate_plugin_manifest_structure()
