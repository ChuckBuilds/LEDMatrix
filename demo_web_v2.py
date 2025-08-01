#!/usr/bin/env python3
"""
LED Matrix Web Interface V2 Demo
Demonstrates the new features and capabilities of the modern web interface.
"""

import os
import time
import json
from src.layout_manager import LayoutManager
from src.display_manager import DisplayManager
from src.config_manager import ConfigManager

def create_demo_config():
    """Create a demo configuration for testing."""
    demo_config = {
        "display": {
            "hardware": {
                "rows": 32,
                "cols": 64,
                "chain_length": 2,
                "parallel": 1,
                "brightness": 95,
                "hardware_mapping": "adafruit-hat-pwm"
            },
            "runtime": {
                "gpio_slowdown": 3
            }
        },
        "schedule": {
            "enabled": True,
            "start_time": "07:00",
            "end_time": "23:00"
        }
    }
    return demo_config

def demo_layout_manager():
    """Demonstrate the layout manager capabilities."""
    print("🎨 LED Matrix Layout Manager Demo")
    print("=" * 50)
    
    # Create layout manager (without actual display for demo)
    layout_manager = LayoutManager()
    
    # Create preset layouts
    print("Creating preset layouts...")
    layout_manager.create_preset_layouts()
    
    # List available layouts
    layouts = layout_manager.list_layouts()
    print(f"Available layouts: {layouts}")
    
    # Show layout previews
    for layout_name in layouts:
        preview = layout_manager.get_layout_preview(layout_name)
        print(f"\n📋 Layout: {layout_name}")
        print(f"   Description: {preview.get('description', 'No description')}")
        print(f"   Elements: {preview.get('element_count', 0)}")
        for element in preview.get('elements', []):
            print(f"   - {element['type']} at {element['position']}")
    
    return layout_manager

def demo_custom_layout():
    """Demonstrate creating a custom layout."""
    print("\n🛠️ Creating Custom Layout Demo")
    print("=" * 50)
    
    layout_manager = LayoutManager()
    
    # Create a custom sports dashboard layout
    sports_layout = [
        {
            'type': 'text',
            'x': 2,
            'y': 2,
            'properties': {
                'text': 'SPORTS',
                'color': [255, 255, 0],
                'font_size': 'normal'
            }
        },
        {
            'type': 'line',
            'x': 0,
            'y': 12,
            'properties': {
                'x2': 128,
                'y2': 12,
                'color': [100, 100, 100]
            }
        },
        {
            'type': 'data_text',
            'x': 2,
            'y': 15,
            'properties': {
                'data_key': 'sports.team1.score',
                'format': 'TB: {value}',
                'color': [0, 255, 0],
                'default': 'TB: --'
            }
        },
        {
            'type': 'data_text',
            'x': 2,
            'y': 24,
            'properties': {
                'data_key': 'sports.team2.score',
                'format': 'DAL: {value}',
                'color': [0, 100, 255],
                'default': 'DAL: --'
            }
        }
    ]
    
    # Save the custom layout
    success = layout_manager.create_layout(
        'sports_dashboard', 
        sports_layout, 
        'Custom sports dashboard showing team scores'
    )
    
    if success:
        print("✅ Custom sports dashboard layout created successfully!")
        
        # Show the layout preview
        preview = layout_manager.get_layout_preview('sports_dashboard')
        print(f"📋 Layout Preview:")
        print(f"   Elements: {preview.get('element_count', 0)}")
        for element in preview.get('elements', []):
            print(f"   - {element['type']} at {element['position']}")
    else:
        print("❌ Failed to create custom layout")
    
    return layout_manager

def demo_web_features():
    """Demonstrate web interface features."""
    print("\n🌐 Web Interface Features Demo")
    print("=" * 50)
    
    features = [
        "🖥️ Real-Time Display Preview",
        "   - Live WebSocket connection",
        "   - Scaled-up preview for visibility",
        "   - Screenshot capture",
        "",
        "✏️ Display Editor Mode",
        "   - Drag-and-drop element placement",
        "   - Real-time property editing",
        "   - Custom layout creation",
        "   - Element palette with multiple types",
        "",
        "📊 System Monitoring",
        "   - CPU temperature tracking",
        "   - Memory usage monitoring",
        "   - Service status indicators",
        "   - Performance metrics",
        "",
        "⚙️ Configuration Management",
        "   - Tabbed interface for organization",
        "   - Visual controls (sliders, toggles)",
        "   - Real-time config updates",
        "   - Instant feedback",
        "",
        "🎨 Modern UI Design",
        "   - Responsive layout",
        "   - Professional styling",
        "   - Smooth animations",
        "   - Color-coded status indicators"
    ]
    
    for feature in features:
        print(feature)
        if feature.startswith("   -"):
            time.sleep(0.1)  # Small delay for effect

def demo_api_endpoints():
    """Show available API endpoints."""
    print("\n🔌 API Endpoints Demo")
    print("=" * 50)
    
    endpoints = {
        "Display Control": [
            "POST /api/display/start - Start the LED matrix",
            "POST /api/display/stop - Stop the LED matrix", 
            "GET /api/display/current - Get current display image"
        ],
        "Editor Mode": [
            "POST /api/editor/toggle - Toggle editor mode",
            "POST /api/editor/preview - Update layout preview"
        ],
        "Configuration": [
            "POST /api/config/save - Save configuration changes",
            "GET /api/system/status - Get system status"
        ],
        "System Actions": [
            "POST /api/system/action - Execute system commands",
            "GET /logs - View system logs"
        ]
    }
    
    for category, apis in endpoints.items():
        print(f"\n📁 {category}:")
        for api in apis:
            print(f"   {api}")

def show_setup_instructions():
    """Show setup instructions."""
    print("\n🚀 Setup Instructions")
    print("=" * 50)
    
    instructions = [
        "1. Install dependencies:",
        "   pip install -r requirements_web_v2.txt",
        "",
        "2. Make startup script executable:",
        "   chmod +x start_web_v2.py",
        "",
        "3. Start the web interface:",
        "   python3 start_web_v2.py",
        "",
        "4. Access the interface:",
        "   Open browser to http://your-pi-ip:5001",
        "",
        "5. Enter Editor Mode:",
        "   - Click 'Enter Editor' button",
        "   - Drag elements from palette",
        "   - Customize properties",
        "   - Save your layout",
        "",
        "6. Monitor your system:",
        "   - Check real-time stats in header",
        "   - View performance metrics",
        "   - Access system logs"
    ]
    
    for instruction in instructions:
        print(instruction)

def main():
    """Main demo function."""
    print("🎯 LED Matrix Web Interface V2 - Complete Demo")
    print("=" * 60)
    print()
    
    # Show features
    demo_web_features()
    
    # Demo layout manager
    layout_manager = demo_layout_manager()
    
    # Demo custom layout creation
    demo_custom_layout()
    
    # Show API endpoints
    demo_api_endpoints()
    
    # Show setup instructions
    show_setup_instructions()
    
    print("\n" + "=" * 60)
    print("🎉 Demo Complete!")
    print("Ready to revolutionize your LED Matrix experience!")
    print("Start the web interface with: python3 start_web_v2.py")
    print("=" * 60)

if __name__ == '__main__':
    main()