#!/usr/bin/env python3
"""
Alternative dependency installer that tries apt packages first,
then falls back to pip with --break-system-packages
"""

import subprocess
import sys
from pathlib import Path

def install_via_apt(package_name):
    """Try to install a package via apt."""
    try:
        # Map pip package names to apt package names
        apt_package_map = {
            'flask': 'python3-flask',
            'flask_socketio': 'python3-flask-socketio',
            'PIL': 'python3-pil',
            'socketio': 'python3-socketio',
            'eventlet': 'python3-eventlet',
            'freetype': 'python3-freetype',
            'psutil': 'python3-psutil',
            'werkzeug': 'python3-werkzeug',
            'numpy': 'python3-numpy',
            'requests': 'python3-requests',
            'python-dateutil': 'python3-dateutil',
            'pytz': 'python3-tz',
            'geopy': 'python3-geopy',
            'unidecode': 'python3-unidecode',
            'websockets': 'python3-websockets',
            'websocket-client': 'python3-websocket-client'
        }
        
        apt_package = apt_package_map.get(package_name, f'python3-{package_name}')
        
        print(f"Trying to install {apt_package} via apt...")
        subprocess.check_call([
            'sudo', 'apt', 'update'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        subprocess.check_call([
            'sudo', 'apt', 'install', '-y', apt_package
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        print(f"Successfully installed {apt_package} via apt")
        return True
        
    except subprocess.CalledProcessError:
        print(f"Failed to install {package_name} via apt, will try pip")
        return False

def install_via_pip(package_name):
    """Install a package via pip with --break-system-packages."""
    try:
        print(f"Installing {package_name} via pip...")
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install', '--break-system-packages', package_name
        ])
        print(f"Successfully installed {package_name} via pip")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to install {package_name} via pip: {e}")
        return False

def check_package_installed(package_name):
    """Check if a package is already installed."""
    try:
        __import__(package_name)
        return True
    except ImportError:
        return False

def main():
    """Main installation function."""
    print("Installing dependencies for LED Matrix Web Interface V2...")
    
    # List of required packages
    required_packages = [
        'flask',
        'flask_socketio', 
        'PIL',
        'socketio',
        'eventlet',
        'freetype',
        'psutil',
        'werkzeug',
        'numpy',
        'requests',
        'python-dateutil',
        'pytz',
        'geopy',
        'unidecode',
        'websockets',
        'websocket-client'
    ]
    
    failed_packages = []
    
    for package in required_packages:
        if check_package_installed(package):
            print(f"{package} is already installed")
            continue
            
        # Try apt first, then pip
        if not install_via_apt(package):
            if not install_via_pip(package):
                failed_packages.append(package)
    
    # Install packages that don't have apt equivalents
    special_packages = [
        'timezonefinder>=6.5.0,<7.0.0',
        'google-auth-oauthlib>=1.2.0,<2.0.0',
        'google-auth-httplib2>=0.2.0,<1.0.0',
        'google-api-python-client>=2.147.0,<3.0.0',
        'spotipy',
        'icalevents',
        'python-engineio'
    ]
    
    for package in special_packages:
        if not install_via_pip(package):
            failed_packages.append(package)
    
    # Install rgbmatrix module from local source
    print("Installing rgbmatrix module...")
    try:
        # Get project root (parent of scripts directory)
        PROJECT_ROOT = Path(__file__).parent.parent
        rgbmatrix_path = PROJECT_ROOT / 'rpi-rgb-led-matrix-master' / 'bindings' / 'python'
        if rgbmatrix_path.exists():
            subprocess.check_call([
                sys.executable, '-m', 'pip', 'install', '--break-system-packages', '-e', str(rgbmatrix_path)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("rgbmatrix module installed successfully")
        else:
            print("Warning: rgbmatrix source not found")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install rgbmatrix module: {e}")
        failed_packages.append('rgbmatrix')
    
    if failed_packages:
        print(f"\nFailed to install the following packages: {failed_packages}")
        print("You may need to install them manually or check your system configuration.")
        return False
    else:
        print("\nAll dependencies installed successfully!")
        return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
