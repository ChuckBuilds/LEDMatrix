#!/usr/bin/env python3
"""
Check what imports are actually in the app.py file on the Pi
"""

from pathlib import Path

# Read the app.py file and check the import lines
app_py_path = Path.home() / 'LEDMatrix' / 'web_interface' / 'app.py'

print(f"🔍 Checking imports in: {app_py_path}")
print(f"📁 File exists: {app_py_path.exists()}")

if app_py_path.exists():
    with open(app_py_path, 'r') as f:
        lines = f.readlines()

    print("\n🔍 Import lines in app.py:")
    for i, line in enumerate(lines, 1):
        if 'from' in line and 'blueprints' in line and 'import' in line:
            print(f"  Line {i}: {line.strip()}")

    print("\n🔍 Blueprint registration lines:")
    for i, line in enumerate(lines, 1):
        if 'register_blueprint' in line:
            print(f"  Line {i}: {line.strip()}")
else:
    print("❌ app.py file not found!")
