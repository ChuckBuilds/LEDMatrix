#!/usr/bin/env python3
"""
Simple runner for LED Matrix Web Interface V2
Handles virtual environment setup and dependency installation
"""

import os
import sys
import subprocess
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main function to set up and run the web interface."""
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    venv_path = script_dir / 'venv_web_v2'
    
    # Create virtual environment if it doesn't exist
    if not venv_path.exists():
        logger.info("Creating virtual environment...")
        subprocess.check_call([
            sys.executable, '-m', 'venv', str(venv_path)
        ])
        logger.info("Virtual environment created successfully")
    
    # Get virtual environment Python and pip paths
    if os.name == 'nt':  # Windows
        venv_python = venv_path / 'Scripts' / 'python.exe'
        venv_pip = venv_path / 'Scripts' / 'pip.exe'
    else:  # Unix/Linux
        venv_python = venv_path / 'bin' / 'python'
        venv_pip = venv_path / 'bin' / 'pip'
    
    # Always install dependencies to ensure everything is up to date
    logger.info("Installing dependencies...")
    try:
        subprocess.check_call([
            str(venv_pip), 'install', '-r', 'requirements_web_v2.txt'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Dependencies installed successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install dependencies: {e}")
        return
    
    # Install rgbmatrix module from local source
    logger.info("Installing rgbmatrix module...")
    try:
        rgbmatrix_path = script_dir / 'rpi-rgb-led-matrix-master' / 'bindings' / 'python'
        subprocess.check_call([
            str(venv_pip), 'install', '-e', str(rgbmatrix_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("rgbmatrix module installed successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install rgbmatrix module: {e}")
        return
    
    # Run the web interface
    logger.info("Starting web interface on http://0.0.0.0:5001")
    subprocess.run([str(venv_python), 'web_interface_v2.py'])

if __name__ == '__main__':
    main() 