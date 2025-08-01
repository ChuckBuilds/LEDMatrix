#!/usr/bin/env python3
"""
Clean setup script for LED Matrix Web Interface V2
Removes existing virtual environment and creates a fresh one with all dependencies
"""

import os
import sys
import subprocess
import logging
import shutil
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main function to set up a clean virtual environment."""
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    venv_path = script_dir / 'venv_web_v2'
    
    # Remove existing virtual environment if it exists
    if venv_path.exists():
        logger.info("Removing existing virtual environment...")
        try:
            shutil.rmtree(venv_path)
            logger.info("Existing virtual environment removed")
        except Exception as e:
            logger.error(f"Failed to remove existing virtual environment: {e}")
            return
    
    # Create new virtual environment
    logger.info("Creating new virtual environment...")
    try:
        subprocess.check_call([
            sys.executable, '-m', 'venv', str(venv_path)
        ])
        logger.info("Virtual environment created successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create virtual environment: {e}")
        return
    
    # Get virtual environment Python and pip paths
    if os.name == 'nt':  # Windows
        venv_python = venv_path / 'Scripts' / 'python.exe'
        venv_pip = venv_path / 'Scripts' / 'pip.exe'
    else:  # Unix/Linux
        venv_python = venv_path / 'bin' / 'python'
        venv_pip = venv_path / 'bin' / 'pip'
    
    # Upgrade pip first
    logger.info("Upgrading pip...")
    try:
        subprocess.check_call([
            str(venv_pip), 'install', '--upgrade', 'pip'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Pip upgraded successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to upgrade pip: {e}")
        return
    
    # Install dependencies
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
    
    # Verify key packages are installed
    logger.info("Verifying installation...")
    test_packages = ['flask', 'freetype', 'PIL']
    for package in test_packages:
        try:
            subprocess.check_call([
                str(venv_python), '-c', f'import {package}'
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"✓ {package} is available")
        except subprocess.CalledProcessError:
            logger.error(f"✗ {package} is NOT available")
    
    logger.info("Setup completed successfully!")
    logger.info("You can now run the web interface with:")
    logger.info("  sudo python3 run_web_v2_simple.py")

if __name__ == '__main__':
    main() 