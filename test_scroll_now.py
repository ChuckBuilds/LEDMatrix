#!/usr/bin/env python3
"""
One-command scroll system test for Raspberry Pi.

Usage: python3 test_scroll_now.py

This script will immediately test the new scroll system on your Pi
and show you the performance improvements.
"""

import sys
import os
import time
import json
import logging
from typing import Optional

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_scroll_system():
    """Quick test of the scroll system."""
    print("🚀 LED Matrix Scroll System - Quick Test")
    print("=" * 50)
    
    try:
        # Import after path setup
        from display_manager import DisplayManager
        # Import scroll base without relative imports
        import importlib.util
        scroll_base_path = os.path.join('src', 'base_classes', 'scroll_base.py')
        spec = importlib.util.spec_from_file_location("scroll_base", scroll_base_path)
        scroll_base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scroll_base)
        BaseScrollController = scroll_base.BaseScrollController
        
        from PIL import Image, ImageDraw
        
        # Simple config
        config = {
            'display': {
                'hardware': {
                    'rows': 32,
                    'cols': 64, 
                    'chain_length': 2,
                    'brightness': 95,
                    'hardware_mapping': 'adafruit-hat-pwm'
                }
            }
        }
        
        # Try to load actual config if available
        config_paths = ['config/config.json', 'config/config.template.json']
        for path in config_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        loaded_config = json.load(f)
                        config.update(loaded_config)
                        print(f"✓ Loaded config from {path}")
                        break
                except:
                    pass
        
        print("✓ Initializing LED matrix...")
        display_manager = DisplayManager(config)
        
        width = display_manager.matrix.width
        height = display_manager.matrix.height
        print(f"✓ Display: {width}x{height} pixels")
        
        # Create test image (medium-long for good test)
        test_width = 2000
        test_image = Image.new('RGB', (test_width, height), (0, 0, 0))
        draw = ImageDraw.Draw(test_image)
        
        # Create colorful test pattern
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for x in range(0, test_width, 50):
            color = colors[(x // 50) % len(colors)]
            draw.rectangle([x, 0, x + 25, height], fill=color)
        
        # Add text
        font = display_manager.regular_font
        text = "NEW SCROLL SYSTEM TEST - Smooth Performance!"
        for x in range(0, test_width, 300):
            draw.text((x + 10, height // 2 - 5), text, fill=(255, 255, 255), font=font)
        
        print(f"✓ Created test image: {test_width}x{height}px")
        
        # Initialize scroll controller
        scroll_config = {
            'scroll_pixels_per_second': 25.0,
            'scroll_target_fps': 100.0,
            'scroll_mode': 'continuous_loop',
            'enable_scroll_metrics': True
        }
        
        controller = BaseScrollController(
            config=scroll_config,
            display_width=width,
            display_height=height,
            content_width=test_width,
            content_height=height,
            debug_name="QuickTest"
        )
        
        print("✓ Scroll system initialized")
        print(f"✓ Target: {scroll_config['scroll_pixels_per_second']}px/s at {scroll_config['scroll_target_fps']} FPS")
        
        # Run test
        print(f"\n🎬 Starting 15-second performance test...")
        print("Watch your LED matrix for smooth scrolling!")
        
        start_time = time.time()
        frame_count = 0
        fps_samples = []
        
        while time.time() - start_time < 15.0:
            frame_start = time.time()
            
            # Update scroll
            scroll_state = controller.update()
            
            # Calculate crop region
            crop_info = controller.get_crop_region()
            source_x = max(0, min(int(crop_info['source_x']), test_width - width))
            
            # Crop and display
            visible = test_image.crop((source_x, 0, source_x + width, height))
            display_manager.image = visible
            display_manager.update_display()
            
            frame_count += 1
            
            # Calculate FPS
            frame_time = time.time() - frame_start
            if frame_time > 0:
                fps = 1.0 / frame_time
                fps_samples.append(fps)
                if len(fps_samples) > 100:
                    fps_samples.pop(0)
            
            # Show progress every 3 seconds
            elapsed = time.time() - start_time
            if frame_count % 300 == 0:
                avg_fps = sum(fps_samples[-50:]) / min(len(fps_samples), 50)
                print(f"  {elapsed:.1f}s: {avg_fps:.1f} FPS, pos={scroll_state['scroll_position']:.1f}")
        
        # Final results
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time
        
        print(f"\n📊 TEST RESULTS:")
        print(f"  Duration: {total_time:.1f}s")
        print(f"  Total frames: {frame_count}")
        print(f"  Average FPS: {avg_fps:.1f}")
        print(f"  Image size: {test_width}x{height}px")
        
        # Performance assessment
        if avg_fps > 90:
            print(f"  Status: ✅ EXCELLENT - New scroll system working perfectly!")
            print(f"  Your LED matrix can handle smooth, high-performance scrolling.")
        elif avg_fps > 60:
            print(f"  Status: ✅ GOOD - Solid performance with new scroll system.")
            print(f"  Consider reducing scroll_target_fps if you want lower CPU usage.")
        elif avg_fps > 30:
            print(f"  Status: ⚠️  ACCEPTABLE - May need optimization.")
            print(f"  Try reducing scroll_pixels_per_second or scroll_target_fps.")
        else:
            print(f"  Status: ❌ POOR - System may be overloaded.")
            print(f"  Check system resources and reduce scroll settings.")
        
        # Memory info
        try:
            import psutil
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            print(f"  Memory usage: {memory_mb:.1f}MB")
        except ImportError:
            pass
        
        print(f"\n🎯 NEXT STEPS:")
        if avg_fps > 60:
            print(f"  • Your system is ready for the new scroll system!")
            print(f"  • Migrate your display managers using the examples")
            print(f"  • Tune scroll_pixels_per_second for your preference")
            print(f"  • Enable scroll_metrics for ongoing monitoring")
        else:
            print(f"  • Reduce scroll settings for better performance")
            print(f"  • Check system load with 'htop'")
            print(f"  • Consider upgrading Pi hardware if very old")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print(f"Make sure you're in the LEDMatrix project directory")
        print(f"and have installed all dependencies:")
        print(f"  pip install -r requirements.txt")
        return False
        
    except Exception as e:
        print(f"❌ Error: {e}")
        print(f"Make sure your LED matrix is connected and working.")
        print(f"Test with: python3 run.py")
        return False


if __name__ == "__main__":
    print("LED Matrix Scroll System - One-Command Test")
    print("Make sure your LED matrix is connected!")
    print()
    
    # Check if running on Pi
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read()
            if 'Raspberry Pi' in model:
                print(f"✓ Running on: {model.strip()}")
            else:
                print(f"⚠️  Not running on Raspberry Pi: {model.strip()}")
    except:
        print("⚠️  Cannot detect Pi model")
    
    print()
    
    try:
        success = test_scroll_system()
        if success:
            print(f"\n🎉 Test completed successfully!")
        else:
            print(f"\n❌ Test failed - check error messages above")
            
    except KeyboardInterrupt:
        print(f"\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
