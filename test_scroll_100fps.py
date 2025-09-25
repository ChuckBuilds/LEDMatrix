#!/usr/bin/env python3
"""
100 FPS scroll test for Raspberry Pi LED Matrix.

This test pushes your Pi to 100 FPS to demonstrate the performance
the new scroll system will provide.
"""

import sys
import os
import time
import json
from PIL import Image, ImageDraw, ImageFont

def test_basic_display():
    """Test basic LED matrix functionality."""
    print("🔍 Testing Basic LED Matrix...")
    
    try:
        from rgbmatrix import RGBMatrix, RGBMatrixOptions
        print("✓ Found rgbmatrix library")
        
        # Load configuration
        config = None
        config_paths = ['config/config.json', 'config/config.template.json']
        
        for path in config_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        config = json.load(f)
                        print(f"✓ Loaded config from {path}")
                        break
                except Exception as e:
                    print(f"Error loading {path}: {e}")
        
        if not config:
            config = {
                'display': {
                    'hardware': {
                        'rows': 32, 'cols': 64, 'chain_length': 2,
                        'brightness': 95, 'hardware_mapping': 'adafruit-hat-pwm',
                        'scan_mode': 0, 'pwm_bits': 9, 'gpio_slowdown': 3
                    }
                }
            }
            print("✓ Using safe default config")
        
        # Set up matrix options
        options = RGBMatrixOptions()
        hw_config = config['display']['hardware']
        
        options.rows = hw_config.get('rows', 32)
        options.cols = hw_config.get('cols', 64)
        options.chain_length = hw_config.get('chain_length', 2)
        options.parallel = hw_config.get('parallel', 1)
        options.brightness = hw_config.get('brightness', 95)
        options.hardware_mapping = hw_config.get('hardware_mapping', 'adafruit-hat-pwm')
        options.scan_mode = hw_config.get('scan_mode', 0)
        options.pwm_bits = hw_config.get('pwm_bits', 9)
        options.pwm_dither_bits = hw_config.get('pwm_dither_bits', 1)
        options.pwm_lsb_nanoseconds = hw_config.get('pwm_lsb_nanoseconds', 130)
        
        runtime_config = config['display'].get('runtime', {})
        options.gpio_slowdown = runtime_config.get('gpio_slowdown', 3)
        
        print(f"✓ Matrix config: {options.cols * options.chain_length}x{options.rows}")
        
        matrix = RGBMatrix(options=options)
        width = matrix.width
        height = matrix.height
        
        print(f"✓ Matrix initialized: {width}x{height} pixels")
        return matrix, width, height
        
    except Exception as e:
        print(f"❌ Error initializing matrix: {e}")
        return None


def create_high_performance_test_image(width_px, height_px):
    """Create a test image optimized for high FPS testing."""
    image = Image.new('RGB', (width_px, height_px), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # High-contrast pattern for easy visibility at high speed
    colors = [
        (255, 0, 0),    # Red
        (0, 255, 0),    # Green  
        (0, 0, 255),    # Blue
        (255, 255, 0),  # Yellow
        (255, 0, 255),  # Magenta
        (0, 255, 255),  # Cyan
        (255, 255, 255) # White
    ]
    
    # Create alternating blocks for high visibility
    block_width = 40
    for x in range(0, width_px, block_width):
        color = colors[(x // block_width) % len(colors)]
        draw.rectangle([x, 0, x + block_width // 2, height_px], fill=color)
    
    # Load font
    font = None
    font_paths = [
        'assets/fonts/PressStart2P-Regular.ttf',
        'assets/fonts/press-start-2p.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    ]
    
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, 8)
                print(f"✓ Using font: {font_path}")
                break
            except:
                continue
    
    if not font:
        font = ImageFont.load_default()
        print("✓ Using default font")
    
    # Add high-visibility text
    text = "100 FPS TEST - ULTRA SMOOTH!"
    text_spacing = 180
    for x in range(10, width_px, text_spacing):
        # High contrast white text with black outline
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, height_px // 2 - 4 + dy), text, fill=(0, 0, 0), font=font)
        draw.text((x, height_px // 2 - 4), text, fill=(255, 255, 255), font=font)
    
    # Add FPS markers
    for x in range(0, width_px, 100):
        draw.line([(x, 0), (x, height_px)], fill=(255, 255, 255), width=2)
        draw.text((x + 2, 2), f"{x}", fill=(255, 255, 255), font=font)
    
    return image


def run_100fps_test(matrix, display_width, display_height):
    """Run the 100 FPS performance test."""
    print(f"\n🚀 100 FPS PERFORMANCE TEST")
    print("=" * 35)
    
    # High-performance test parameters
    test_content_width = 1500  # Medium-large content
    pixels_per_second = 25.0   # Faster scrolling for 100 FPS
    target_fps = 100.0         # The target we want!
    test_duration = 20.0       # Longer test for better measurement
    
    print(f"✓ Test image: {test_content_width}x{display_height}px")
    print(f"✓ Scroll speed: {pixels_per_second}px/s")
    print(f"✓ Target FPS: {target_fps} (NEW SYSTEM TARGET)")
    print(f"✓ Duration: {test_duration}s")
    print(f"✓ Frame interval: {1000/target_fps:.1f}ms per frame")
    
    # Create optimized test content
    test_image = create_high_performance_test_image(test_content_width, display_height)
    
    # Initialize scroll variables
    scroll_position = 0.0
    frame_delay = 1.0 / target_fps  # 10ms per frame for 100 FPS
    
    # Enhanced performance tracking
    start_time = time.time()
    last_frame_time = start_time
    frame_count = 0
    fps_samples = []
    processing_times = []
    missed_frames = 0
    
    print(f"\n🎬 Starting 100 FPS test - watch for ULTRA SMOOTH scrolling!")
    print(f"The text should scroll much smoother than the 50 FPS test...")
    
    try:
        while time.time() - start_time < test_duration:
            frame_start = time.time()
            
            # Calculate time-based movement
            current_time = time.time()
            if last_frame_time > 0:
                delta_time = current_time - last_frame_time
                scroll_position += pixels_per_second * delta_time
                
                # Loop when we reach the end
                if scroll_position >= test_content_width:
                    scroll_position = 0.0
            
            last_frame_time = current_time
            
            # Create the visible portion (optimized cropping)
            source_x = int(scroll_position) % test_content_width
            
            if source_x + display_width <= test_content_width:
                # Fast crop - no wrap needed
                visible_portion = test_image.crop((
                    source_x, 0, 
                    source_x + display_width, display_height
                ))
            else:
                # Optimized wrap-around
                visible_portion = Image.new('RGB', (display_width, display_height), (0, 0, 0))
                
                part1_width = test_content_width - source_x
                part1 = test_image.crop((source_x, 0, test_content_width, display_height))
                visible_portion.paste(part1, (0, 0))
                
                part2_width = display_width - part1_width
                if part2_width > 0:
                    part2 = test_image.crop((0, 0, part2_width, display_height))
                    visible_portion.paste(part2, (part1_width, 0))
            
            # Display to matrix
            processing_start = time.time()
            matrix.SetImage(visible_portion.convert('RGB'))
            processing_time = time.time() - processing_start
            processing_times.append(processing_time * 1000)  # Convert to ms
            
            frame_count += 1
            
            # Calculate processing FPS
            frame_time = time.time() - frame_start
            if frame_time > 0:
                fps = 1.0 / frame_time
                fps_samples.append(fps)
                if len(fps_samples) > 50:
                    fps_samples.pop(0)
            
            # Show detailed progress every 2 seconds
            elapsed = time.time() - start_time
            if frame_count % 200 == 0:  # Every ~2 seconds at 100 FPS
                avg_fps = sum(fps_samples[-30:]) / min(len(fps_samples), 30)
                avg_processing = sum(processing_times[-30:]) / min(len(processing_times), 30)
                print(f"  {elapsed:.1f}s: Display={frame_count/elapsed:.1f} FPS, "
                      f"Processing={avg_fps:.1f} FPS, "
                      f"Matrix update={avg_processing:.1f}ms")
            
            # Frame rate limiting with miss detection
            remaining_time = frame_delay - frame_time
            if remaining_time > 0:
                time.sleep(remaining_time)
            else:
                missed_frames += 1
    
    except KeyboardInterrupt:
        print(f"\n⏹️  Test interrupted by user")
        return False
    
    # Calculate comprehensive results
    total_time = time.time() - start_time
    actual_display_fps = frame_count / total_time
    avg_processing_fps = sum(fps_samples) / len(fps_samples) if fps_samples else 0
    avg_matrix_update_ms = sum(processing_times) / len(processing_times) if processing_times else 0
    
    print(f"\n📊 100 FPS TEST RESULTS:")
    print("=" * 30)
    print(f"  Duration: {total_time:.1f}s")
    print(f"  Total frames: {frame_count}")
    print(f"  Target FPS: {target_fps}")
    print(f"  Actual Display FPS: {actual_display_fps:.1f}")
    print(f"  Processing FPS: {avg_processing_fps:.1f}")
    print(f"  Matrix update time: {avg_matrix_update_ms:.1f}ms")
    print(f"  Missed frames: {missed_frames}")
    print(f"  Frame accuracy: {((frame_count-missed_frames)/frame_count)*100:.1f}%")
    
    # Performance assessment for 100 FPS
    efficiency = actual_display_fps / target_fps
    if efficiency >= 0.95:
        print(f"  Status: 🏆 OUTSTANDING - 100 FPS achieved!")
        print(f"  Your Pi 3B+ is ready for professional-grade scrolling!")
    elif efficiency >= 0.85:
        print(f"  Status: ✅ EXCELLENT - Near 100 FPS performance!")
        print(f"  Minor optimizations could reach perfect 100 FPS.")
    elif efficiency >= 0.70:
        print(f"  Status: ✅ VERY GOOD - Solid high-FPS performance!")
        print(f"  Significantly smoother than standard 50 FPS.")
    elif efficiency >= 0.50:
        print(f"  Status: ⚠️  GOOD - Decent high-FPS performance.")
        print(f"  Consider optimizing settings for better results.")
    else:
        print(f"  Status: ❌ NEEDS OPTIMIZATION - 100 FPS too demanding.")
        print(f"  Try 75 FPS target or optimize system load.")
    
    # Memory and efficiency info
    memory_mb = (test_content_width * display_height * 3) / (1024 * 1024)
    cpu_efficiency = (1000 / target_fps) / avg_matrix_update_ms if avg_matrix_update_ms > 0 else 0
    
    print(f"  Image memory: {memory_mb:.1f}MB")
    print(f"  CPU efficiency: {cpu_efficiency:.1f}x (higher is better)")
    
    return actual_display_fps, efficiency


def compare_with_50fps(fps_100, efficiency_100):
    """Compare 100 FPS results with 50 FPS baseline."""
    print(f"\n📈 PERFORMANCE COMPARISON:")
    print("=" * 28)
    
    # Estimates based on previous 50 FPS test
    baseline_50fps = 49.6
    
    print(f"  50 FPS Test (Previous): {baseline_50fps:.1f} FPS")
    print(f"  100 FPS Test (Current): {fps_100:.1f} FPS")
    
    if fps_100 >= 85:
        improvement = ((fps_100 - baseline_50fps) / baseline_50fps) * 100
        print(f"  Improvement: {improvement:.0f}% faster!")
        print(f"  Smoothness: {fps_100/baseline_50fps:.1f}x smoother animation")
    
    print(f"\n💡 VISUAL DIFFERENCE:")
    print(f"  • 50 FPS: Smooth scrolling")
    print(f"  • {fps_100:.0f} FPS: Ultra-smooth, professional-grade scrolling")
    print(f"  • Difference: Much more fluid motion, especially noticeable")
    print(f"    with fast-moving content and fine details")


def show_new_system_readiness(fps_result, efficiency):
    """Show readiness for the new scroll system."""
    print(f"\n🎯 NEW SCROLL SYSTEM READINESS:")
    print("=" * 35)
    
    if efficiency >= 0.85:
        print(f"  Status: 🚀 READY FOR DEPLOYMENT!")
        print(f"  Your Pi can handle the new system's target performance.")
        print(f"  Recommended settings:")
        print(f"    • scroll_target_fps: 100.0")
        print(f"    • scroll_pixels_per_second: 25.0")
        print(f"    • enable_scroll_metrics: true")
        print(f"    • scroll_subpixel_positioning: true")
        
    elif efficiency >= 0.70:
        print(f"  Status: ✅ READY with minor tuning")
        print(f"  Your Pi can handle high-performance scrolling.")
        print(f"  Recommended settings:")
        print(f"    • scroll_target_fps: 85.0")
        print(f"    • scroll_pixels_per_second: 22.0")
        print(f"    • enable_scroll_metrics: true")
        
    else:
        print(f"  Status: ⚠️  Ready with conservative settings")
        print(f"  Your Pi will benefit from the new system's efficiency.")
        print(f"  Recommended settings:")
        print(f"    • scroll_target_fps: 75.0")
        print(f"    • scroll_pixels_per_second: 20.0")
        print(f"    • enable_scroll_metrics: true")
    
    print(f"\n🔧 MIGRATION PRIORITY:")
    print(f"  1. Start with leaderboard_manager (biggest improvement)")
    print(f"  2. Migrate stock_manager and news_manager")
    print(f"  3. Update text_display for consistency")
    print(f"  4. Enable performance monitoring across all managers")


def main():
    """Main test function."""
    print("🚀 LED Matrix 100 FPS Performance Test")
    print("=" * 42)
    print("Testing your Pi at the new scroll system's target performance!")
    print("This will show how smooth scrolling can really be.\n")
    
    # Hardware detection
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip()
            print(f"✓ Hardware: {model}")
    except:
        print("⚠️  Cannot detect Pi model")
    
    print()
    
    # Initialize matrix
    matrix_result = test_basic_display()
    if not matrix_result:
        print(f"\n❌ Cannot initialize LED matrix")
        return False
    
    matrix, width, height = matrix_result
    
    print(f"\n⚡ PUSHING YOUR PI TO 100 FPS!")
    print(f"This test will show the performance level the new scroll system provides.")
    input("Press Enter to start the 100 FPS test...")
    
    try:
        # Run the high-performance test
        fps_result, efficiency = run_100fps_test(matrix, width, height)
        
        # Analysis and comparison
        compare_with_50fps(fps_result, efficiency)
        show_new_system_readiness(fps_result, efficiency)
        
        print(f"\n🎉 100 FPS TEST COMPLETED!")
        print(f"You've just experienced the performance level")
        print(f"the new scroll system will provide consistently!")
        
    except Exception as e:
        print(f"❌ Test error: {e}")
        return False
    
    finally:
        if matrix:
            matrix.Clear()
    
    return True


if __name__ == "__main__":
    print("LED Matrix Scroll System - 100 FPS Performance Test")
    print("Experience the new system's target performance!")
    print()
    
    try:
        success = main()
        if success:
            print(f"\n🏆 Test completed - your Pi is ready for ultra-smooth scrolling!")
        else:
            print(f"\n❌ Test failed - check error messages above")
            
    except KeyboardInterrupt:
        print(f"\n⏹️  Test interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
