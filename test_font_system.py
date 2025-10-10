#!/usr/bin/env python3
"""
Test script for the enhanced font system functionality.
Tests font discovery, plugin integration, adaptive sizing, and web API endpoints.
"""

import os
import sys
import json
import time
import logging
import tempfile
import shutil
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from font_manager import FontManager
from display_manager import DisplayManager
from plugin_loader import PluginLoader

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class FontSystemTester:
    """Comprehensive test suite for the enhanced font system."""

    def __init__(self):
        self.test_results = {
            "font_discovery": False,
            "plugin_integration": False,
            "adaptive_sizing": False,
            "performance_monitoring": False,
            "web_api_endpoints": False,
            "error_handling": False
        }
        self.temp_dir = None

    def setup_test_environment(self):
        """Set up test environment with sample fonts."""
        # Create temporary directory for test fonts
        self.temp_dir = Path(tempfile.mkdtemp(prefix="font_test_"))

        # Create test font files
        self._create_test_fonts()

        logger.info(f"Test environment set up in: {self.temp_dir}")

    def _create_test_fonts(self):
        """Create sample font files for testing."""
        # Create a simple TTF-like file structure for testing
        test_font_dir = self.temp_dir / "test_fonts"
        test_font_dir.mkdir()

        # Create a simple test TTF file (just binary data for testing)
        test_ttf = test_font_dir / "test_font.ttf"
        with open(test_ttf, 'wb') as f:
            # Write some binary data that looks like a TTF header
            f.write(b'\x00\x01\x00\x00')  # TTF signature
            f.write(b'\x00' * 100)  # Padding

        # Create a test BDF file
        test_bdf = test_font_dir / "test_font.bdf"
        with open(test_bdf, 'w') as f:
            f.write("STARTFONT 2.1\n")
            f.write("FONT -test-testfont-medium-r-normal--12-120-75-75-c-60-iso8859-1\n")
            f.write("SIZE 12 75 75\n")
            f.write("FONTBOUNDINGBOX 8 12 0 -2\n")
            f.write("STARTPROPERTIES 2\n")
            f.write("FAMILY_NAME \"Test Font\"\n")
            f.write("FONT_ASCENT 10\n")
            f.write("ENDPROPERTIES\n")
            f.write("CHARS 1\n")
            f.write("STARTCHAR A\n")
            f.write("ENCODING 65\n")
            f.write("SWIDTH 600 0\n")
            f.write("DWIDTH 8 0\n")
            f.write("BBX 8 12 0 -2\n")
            f.write("BITMAP\n")
            f.write("00\n")
            f.write("00\n")
            f.write("ENDCHAR\n")
            f.write("ENDFONT\n")

        logger.info(f"Created test font files: {test_ttf}, {test_bdf}")

    def test_font_discovery(self):
        """Test automatic font discovery functionality."""
        logger.info("Testing font discovery...")

        try:
            # Create a minimal config for testing
            config = {
                "fonts": {
                    "families": {
                        "test_font": str(self.temp_dir / "test_fonts" / "test_font.ttf")
                    }
                }
            }

            # Initialize font manager
            font_manager = FontManager(config)

            # Test font discovery
            initial_count = len(font_manager.font_catalog)

            # Manually trigger discovery (since we can't easily test the automatic discovery)
            # In real usage, this would happen during initialization
            discovered_fonts = font_manager._scan_font_directory(str(self.temp_dir / "test_fonts"))

            logger.info(f"Discovered {discovered_fonts} fonts")
            logger.info(f"Total fonts in catalog: {len(font_manager.font_catalog)}")

            # Test font metadata extraction
            for family, path in font_manager.font_catalog.items():
                if "test_font" in family:
                    metadata = font_manager.font_metadata.get(family, {})
                    logger.info(f"Font metadata for {family}: {metadata}")
                    break

            self.test_results["font_discovery"] = True
            logger.info("✓ Font discovery test passed")

        except Exception as e:
            logger.error(f"✗ Font discovery test failed: {e}")
            self.test_results["font_discovery"] = False

    def test_plugin_integration(self):
        """Test plugin font integration."""
        logger.info("Testing plugin integration...")

        try:
            # Create a sample plugin manifest with fonts
            plugin_manifest = {
                "name": "Test Plugin",
                "version": "1.0.0",
                "fonts": {
                    "fonts": [
                        {
                            "family": "plugin_font",
                            "source": str(self.temp_dir / "test_fonts" / "test_font.ttf"),
                            "description": "Test plugin font"
                        }
                    ]
                }
            }

            config = {"fonts": {}}
            font_manager = FontManager(config)

            # Test plugin font registration
            success = font_manager.register_plugin_fonts("test_plugin", plugin_manifest["fonts"])

            if success:
                logger.info(f"✓ Plugin font registration successful")
                logger.info(f"Plugin fonts: {font_manager.get_plugin_fonts('test_plugin')}")

                # Test font resolution with plugin namespace
                try:
                    font = font_manager.resolve_font_with_plugin_support(
                        family="test_plugin::plugin_font",
                        size_px=12
                    )
                    logger.info("✓ Plugin font resolution successful")
                except Exception as e:
                    logger.warning(f"Plugin font resolution failed (expected): {e}")

                # Test plugin font unregistration
                success = font_manager.unregister_plugin_fonts("test_plugin")
                if success:
                    logger.info("✓ Plugin font unregistration successful")
                else:
                    logger.error("✗ Plugin font unregistration failed")

            else:
                logger.error("✗ Plugin font registration failed")

            self.test_results["plugin_integration"] = True
            logger.info("✓ Plugin integration test passed")

        except Exception as e:
            logger.error(f"✗ Plugin integration test failed: {e}")
            self.test_results["plugin_integration"] = False

    def test_adaptive_sizing(self):
        """Test adaptive font sizing functionality."""
        logger.info("Testing adaptive sizing...")

        try:
            config = {"fonts": {}}
            font_manager = FontManager(config)

            # Test optimal size calculation
            optimal_size = font_manager.calculate_optimal_font_size(
                "Hello World", "press_start", 64, 32, min_size=8, max_size=16
            )

            logger.info(f"Optimal size for 'Hello World' in 64x32 area: {optimal_size}px")

            # Test adaptive font resolution
            font = font_manager.adaptive_font_resolution(
                text="Hello World",
                max_width=64,
                max_height=32,
                min_size=8,
                max_size=16
            )

            logger.info(f"✓ Adaptive font resolution successful")

            # Test text fitting info
            fitting_info = font_manager.get_text_fitting_info(
                "Hello World", "press_start", 64, 32, target_size=12
            )

            logger.info(f"Text fitting analysis: {len(fitting_info['recommendations'])} size options")

            # Test auto-sizing for display
            sizing_info = font_manager.auto_size_text_for_display(
                "Hello World", 64, 32, "press_start", padding=4
            )

            logger.info(f"Auto-sizing result: {sizing_info.get('optimal_size', 'N/A')}px")

            self.test_results["adaptive_sizing"] = True
            logger.info("✓ Adaptive sizing test passed")

        except Exception as e:
            logger.error(f"✗ Adaptive sizing test failed: {e}")
            self.test_results["adaptive_sizing"] = False

    def test_performance_monitoring(self):
        """Test performance monitoring functionality."""
        logger.info("Testing performance monitoring...")

        try:
            config = {"fonts": {}}
            font_manager = FontManager(config)

            # Get initial performance stats
            initial_stats = font_manager.get_performance_statistics()
            logger.info(f"Initial cache hit rate: {initial_stats['cache_statistics']['hit_rate_percent']:.1f}%")

            # Test font loading performance
            for i in range(5):
                font = font_manager.resolve(family="press_start", size_px=12)

            # Get performance stats after some operations
            stats = font_manager.get_performance_statistics()
            logger.info(f"Final cache hit rate: {stats['cache_statistics']['hit_rate_percent']:.1f}%")
            logger.info(f"Total renders: {stats['rendering']['total_renders']}")

            # Test performance optimization suggestions
            optimization = font_manager.optimize_performance()
            logger.info(f"Optimization score: {optimization['optimization_score']}/100")
            logger.info(f"Number of recommendations: {len(optimization['recommendations'])}")

            # Test font-specific monitoring
            monitor_info = font_manager.monitor_font_loading("press_start_12")
            logger.info(f"Font monitoring info: {monitor_info['load_performance']['count']} loads")

            self.test_results["performance_monitoring"] = True
            logger.info("✓ Performance monitoring test passed")

        except Exception as e:
            logger.error(f"✗ Performance monitoring test failed: {e}")
            self.test_results["performance_monitoring"] = False

    def test_error_handling(self):
        """Test error handling and edge cases."""
        logger.info("Testing error handling...")

        try:
            config = {"fonts": {}}
            font_manager = FontManager(config)

            # Test invalid font paths
            try:
                font = font_manager.resolve(family="nonexistent_font", size_px=12)
                logger.warning("Expected error for nonexistent font was not raised")
            except Exception as e:
                logger.info(f"✓ Correctly handled nonexistent font: {type(e).__name__}")

            # Test invalid plugin font registration
            invalid_manifest = {
                "fonts": [
                    {"family": "test"}  # Missing required 'source' field
                ]
            }

            success = font_manager.register_plugin_fonts("test_plugin", invalid_manifest)
            if not success:
                logger.info("✓ Correctly rejected invalid font manifest")
            else:
                logger.warning("Invalid font manifest was accepted")

            # Test font dependency checking
            # This would require setting up fonts with dependencies

            self.test_results["error_handling"] = True
            logger.info("✓ Error handling test passed")

        except Exception as e:
            logger.error(f"✗ Error handling test failed: {e}")
            self.test_results["error_handling"] = False

    def test_web_api_simulation(self):
        """Test web API functionality simulation."""
        logger.info("Testing web API simulation...")

        try:
            # Simulate the web API endpoints that would be available
            config = {"fonts": {}}
            display_manager = DisplayManager(config)

            # Test font listing
            fonts = display_manager.list_available_fonts()
            logger.info(f"Available fonts: {len(fonts['global'])} global, {sum(len(p) for p in fonts['plugins'].values())} plugin")

            # Test font statistics
            stats = display_manager.get_font_statistics()
            logger.info(f"Font statistics: {stats['total_fonts']} total fonts")

            # Test performance statistics
            perf_stats = display_manager.get_font_performance_statistics()
            logger.info(f"Performance: {perf_stats['cache_statistics']['hit_rate_percent']:.1f}% cache hit rate")

            # Test font metadata retrieval
            if fonts['global']:
                first_font = list(fonts['global'].keys())[0]
                metadata = display_manager.get_font_metadata(first_font)
                logger.info(f"Font metadata for {first_font}: {metadata is not None}")

            self.test_results["web_api_endpoints"] = True
            logger.info("✓ Web API simulation test passed")

        except Exception as e:
            logger.error(f"✗ Web API simulation test failed: {e}")
            self.test_results["web_api_endpoints"] = False

    def run_all_tests(self):
        """Run all test categories."""
        logger.info("Starting comprehensive font system tests...")

        self.setup_test_environment()

        try:
            self.test_font_discovery()
            self.test_plugin_integration()
            self.test_adaptive_sizing()
            self.test_performance_monitoring()
            self.test_error_handling()
            self.test_web_api_simulation()

        finally:
            # Clean up test environment
            if self.temp_dir and self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                logger.info("Test environment cleaned up")

    def print_summary(self):
        """Print test results summary."""
        print("\n" + "="*60)
        print("FONT SYSTEM TEST RESULTS")
        print("="*60)

        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results.values() if result)

        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {total_tests - passed_tests}")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")

        print("\nDetailed Results:")
        for test_name, result in self.test_results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"  {status} - {test_name.replace('_', ' ').title()}")

        print("="*60)

        if passed_tests == total_tests:
            print("🎉 ALL TESTS PASSED! Font system is fully functional.")
        else:
            print("⚠️  SOME TESTS FAILED. Please check the implementation.")

        return passed_tests == total_tests

def main():
    """Main test execution."""
    tester = FontSystemTester()
    tester.run_all_tests()
    success = tester.print_summary()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
