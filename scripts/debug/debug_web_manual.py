#!/usr/bin/env python3
"""
Web Interface Manual Debug Script
Run this to diagnose why web_interface/start.py isn't working
"""

import sys
import os
import traceback
from pathlib import Path

def main():
    print("🔍 LED Matrix Web Interface Debug Tool")
    print("=" * 50)

    # Change to project root (where this script is located)
    project_root = Path(__file__).parent.resolve()
    os.chdir(project_root)
    print(f"📁 Working directory: {os.getcwd()}")

    # Add to Python path
    sys.path.insert(0, str(project_root))
    print(f"🔗 Python path includes: {project_root}")

    print("\n1. Testing basic imports...")
    try:
        import flask
        print(f"   ✅ Flask: {flask.__version__}")
    except ImportError as e:
        print(f"   ❌ Flask missing: {e}")
        return False

    try:
        from src.config_manager import ConfigManager
        print("   ✅ ConfigManager imported")
    except Exception as e:
        print(f"   ❌ ConfigManager failed: {e}")
        traceback.print_exc()
        return False

    print("\n2. Testing web interface imports...")
    try:
        from web_interface.app import app
        print("   ✅ web_interface.app imported")
        print(f"   📋 App object: {app}")
    except Exception as e:
        print(f"   ❌ web_interface.app failed: {e}")
        traceback.print_exc()
        return False

    print("\n3. Checking config...")
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        print("   ✅ Config loaded")

        autostart = config.get('web_display_autostart', False)
        print(f"   🔧 web_display_autostart: {autostart}")
    except Exception as e:
        print(f"   ❌ Config check failed: {e}")
        traceback.print_exc()
        return False

    print("\n4. Testing Flask startup...")
    try:
        print("   🚀 Starting Flask app...")
        print("   📍 Will run on: http://0.0.0.0:5000")
        print("   ⏹️  Press Ctrl+C to stop")

        # Run the app (this should start the server)
        app.run(host='0.0.0.0', port=5000, debug=True)

    except KeyboardInterrupt:
        print("\n   ⏹️  Server stopped by user")
        return True
    except Exception as e:
        print(f"   ❌ Flask startup failed: {e}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = main()
        if success:
            print("\n✅ Debug completed successfully")
        else:
            print("\n❌ Debug found issues - check output above")
    except Exception as e:
        print(f"\n💥 Debug script crashed: {e}")
        traceback.print_exc()
