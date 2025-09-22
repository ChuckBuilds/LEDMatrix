#!/usr/bin/env python3
"""
Test scroll performance to identify bottlenecks.
"""

import time

def test_scroll_performance():
    """Test the actual scroll performance"""
    print("=== SCROLL PERFORMANCE ANALYSIS ===\n")
    
    # Simulate the scroll behavior
    scroll_speed = 1  # pixels per frame
    total_width = 2000  # Example content width
    display_width = 128
    
    print(f"Test Configuration:")
    print(f"  scroll_speed: {scroll_speed} pixels/frame")
    print(f"  content_width: {total_width}px")
    print(f"  display_width: {display_width}px")
    
    # Test frame-based scrolling (current implementation)
    print(f"\nFrame-based scrolling simulation:")
    scroll_position = 0
    frame_count = 0
    end_position = total_width - display_width
    
    start_time = time.time()
    
    while scroll_position < end_position and frame_count < 10000:  # Safety limit
        scroll_position += scroll_speed
        frame_count += 1
    
    elapsed_time = time.time() - start_time
    actual_fps = frame_count / elapsed_time if elapsed_time > 0 else 0
    
    print(f"  Frames simulated: {frame_count}")
    print(f"  Time elapsed: {elapsed_time:.3f}s")
    print(f"  Actual FPS: {actual_fps:.1f}")
    print(f"  Scroll distance: {scroll_position}px")
    print(f"  Effective speed: {scroll_position/elapsed_time:.1f} px/s")
    
    # Test time-based scrolling (old implementation)
    print(f"\nTime-based scrolling simulation:")
    scroll_position = 0
    frame_count = 0
    scroll_delay = 0.01  # 0.01 seconds per frame
    
    start_time = time.time()
    
    while scroll_position < end_position and frame_count < 10000:
        current_time = time.time()
        if current_time - start_time >= frame_count * scroll_delay:
            scroll_position += scroll_speed
            frame_count += 1
    
    elapsed_time = time.time() - start_time
    actual_fps = frame_count / elapsed_time if elapsed_time > 0 else 0
    
    print(f"  Frames simulated: {frame_count}")
    print(f"  Time elapsed: {elapsed_time:.3f}s")
    print(f"  Actual FPS: {actual_fps:.1f}")
    print(f"  Scroll distance: {scroll_position}px")
    print(f"  Effective speed: {scroll_position/elapsed_time:.1f} px/s")
    
    print(f"\n=== ANALYSIS ===")
    print(f"Frame-based scrolling should be much faster and smoother")
    print(f"If you're seeing slow scrolling, the bottleneck might be:")
    print(f"  1. Display hardware refresh rate limits")
    print(f"  2. Image processing overhead (crop/paste operations)")
    print(f"  3. Display controller loop delays")
    print(f"  4. Other managers interfering with timing")
    
    return True

def main():
    """Run the performance test"""
    try:
        test_scroll_performance()
        return 0
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
