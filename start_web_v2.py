#!/usr/bin/env python3
"""
LED Matrix Web Interface V2 Startup Script
Modern, lightweight web interface with real-time display preview and editor mode.
"""

import os
import sys
import subprocess
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/web_interface_v2.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def setup_virtual_environment():
    """Set up a virtual environment for the web interface."""
    venv_path = Path(__file__).parent / 'venv_web_v2'
    
    if not venv_path.exists():
        logger.info("Creating virtual environment...")
        try:
            subprocess.check_call([
                sys.executable, '-m', 'venv', str(venv_path)
            ])
            logger.info("Virtual environment created successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create virtual environment: {e}")
            return None
    
    return venv_path

def get_venv_python(venv_path):
    """Get the Python executable path from the virtual environment."""
    if os.name == 'nt':  # Windows
        return venv_path / 'Scripts' / 'python.exe'
    else:  # Unix/Linux
        return venv_path / 'bin' / 'python'

def get_venv_pip(venv_path):
    """Get the pip executable path from the virtual environment."""
    if os.name == 'nt':  # Windows
        return venv_path / 'Scripts' / 'pip.exe'
    else:  # Unix/Linux
        return venv_path / 'bin' / 'pip'

def check_dependencies(venv_path):
    """Check if required dependencies are installed in the virtual environment."""
    required_packages = [
        'flask',
        'flask_socketio',
        'PIL',
        'socketio',
        'eventlet',
        'freetype'
    ]
    
    # Use the virtual environment's Python to check imports
    venv_python = get_venv_python(venv_path)
    
    missing_packages = []
    for package in required_packages:
        try:
            subprocess.check_call([
                str(venv_python), '-c', f'import {package}'
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            missing_packages.append(package)
    
    if missing_packages:
        logger.warning(f"Missing packages: {missing_packages}")
        logger.info("Installing missing packages in virtual environment...")
        try:
            venv_pip = get_venv_pip(venv_path)
            subprocess.check_call([
                str(venv_pip), 'install', '-r', 'requirements_web_v2.txt'
            ])
            logger.info("Dependencies installed successfully")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install dependencies: {e}")
            return False
    
    # Install rgbmatrix module from local source
    logger.info("Installing rgbmatrix module...")
    try:
        venv_pip = get_venv_pip(venv_path)
        rgbmatrix_path = Path(__file__).parent / 'rpi-rgb-led-matrix-master' / 'bindings' / 'python'
        subprocess.check_call([
            str(venv_pip), 'install', '-e', str(rgbmatrix_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("rgbmatrix module installed successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install rgbmatrix module: {e}")
        return False
    
    return True

def check_permissions():
    """Check if we have necessary permissions for system operations."""
    try:
        # Test sudo access
        result = subprocess.run(['sudo', '-n', 'true'], capture_output=True)
        if result.returncode != 0:
            logger.warning("Sudo access not available. Some system features may not work.")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking permissions: {e}")
        return False

def main():
    """Main startup function."""
    logger.info("Starting LED Matrix Web Interface V2...")
    
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Set up virtual environment
    venv_path = setup_virtual_environment()
    if not venv_path:
        logger.error("Failed to set up virtual environment. Exiting.")
        sys.exit(1)
    
    # Check dependencies in virtual environment
    if not check_dependencies(venv_path):
        logger.error("Dependency check failed. Exiting.")
        sys.exit(1)
    
    # Check permissions
    check_permissions()
    
    # Import and start the web interface using the virtual environment's Python
    try:
        venv_python = get_venv_python(venv_path)
        logger.info("Web interface loaded successfully")
        
        # Start the server using the virtual environment's Python
        logger.info("Starting web server on http://0.0.0.0:5001")
        subprocess.run([
            str(venv_python), 'web_interface_v2.py'
        ])
        
    except Exception as e:
        logger.error(f"Failed to start web interface: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()