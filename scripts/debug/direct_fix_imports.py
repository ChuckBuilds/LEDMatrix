#!/usr/bin/env python3
"""
Direct fix for import issues - manually edit the app.py file
"""

import os
from pathlib import Path

def fix_imports():
    app_py_path = Path.home() / 'LEDMatrix' / 'web_interface' / 'app.py'

    print(f"🔧 Directly fixing imports in: {app_py_path}")

    # Read the file
    with open(app_py_path, 'r') as f:
        lines = f.readlines()

    # Find and fix the import lines
    fixed = False
    for i, line in enumerate(lines, 1):
        if 'from blueprints.pages_v3 import' in line:
            lines[i-1] = "from web_interface.blueprints.pages_v3 import pages_v3\n"
            print(f"✅ Fixed line {i}: from blueprints.pages_v3 import → from web_interface.blueprints.pages_v3 import")
            fixed = True
        elif 'from blueprints.api_v3 import' in line:
            lines[i-1] = "from web_interface.blueprints.api_v3 import api_v3\n"
            print(f"✅ Fixed line {i}: from blueprints.api_v3 import → from web_interface.blueprints.api_v3 import")
            fixed = True

    if not fixed:
        print("❌ No import lines found to fix")
        return False

    # Write the fixed file back
    with open(app_py_path, 'w') as f:
        f.writelines(lines)

    print("✅ File updated successfully")
    return True

def verify_fix():
    print("\n🔍 Verifying the fix...")
    os.system("python3 check_imports.py")

if __name__ == "__main__":
    if fix_imports():
        print("\n🧹 Clearing Python cache...")
        os.system("find ~/LEDMatrix -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true")
        os.system("find ~/LEDMatrix -name '*.pyc' -delete 2>/dev/null || true")

        print("\n✅ Imports fixed and cache cleared!")
        verify_fix()

        print("\n🚀 Now try running the web interface:")
        print("cd ~/LEDMatrix")
        print("python3 web_interface/start.py")
    else:
        print("\n❌ Fix failed")
