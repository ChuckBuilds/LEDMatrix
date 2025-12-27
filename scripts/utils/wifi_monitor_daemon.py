#!/usr/bin/env python3
"""
WiFi Monitor Daemon

Monitors WiFi connection status and automatically enables/disables access point mode
when there is no active WiFi connection.
"""

import sys
import time
import logging
import signal
from pathlib import Path

# Add project root to path (parent of scripts/utils/)
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.wifi_manager import WiFiManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/var/log/ledmatrix-wifi-monitor.log')
    ]
)

logger = logging.getLogger(__name__)

class WiFiMonitorDaemon:
    """Daemon to monitor WiFi and manage AP mode"""
    
    def __init__(self, check_interval=30):
        """
        Initialize the WiFi monitor daemon
        
        Args:
            check_interval: Seconds between WiFi status checks
        """
        self.check_interval = check_interval
        self.wifi_manager = WiFiManager()
        self.running = True
        self.last_state = None
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
    
    def run(self):
        """Main daemon loop"""
        logger.info("WiFi Monitor Daemon started")
        logger.info(f"Check interval: {self.check_interval} seconds")
        
        while self.running:
            try:
                # Check WiFi status and manage AP mode
                state_changed = self.wifi_manager.check_and_manage_ap_mode()
                
                # Get current status for logging
                status = self.wifi_manager.get_wifi_status()
                ethernet_connected = self.wifi_manager._is_ethernet_connected()
                current_state = {
                    'connected': status.connected,
                    'ethernet_connected': ethernet_connected,
                    'ap_active': status.ap_mode_active,
                    'ssid': status.ssid
                }
                
                # Log state changes
                if current_state != self.last_state:
                    if status.connected:
                        logger.info(f"WiFi connected: {status.ssid} (IP: {status.ip_address})")
                    else:
                        logger.info("WiFi disconnected")
                    
                    if ethernet_connected:
                        logger.info("Ethernet connected")
                    else:
                        logger.debug("Ethernet disconnected")
                    
                    if status.ap_mode_active:
                        logger.info("AP mode active")
                    else:
                        logger.debug("AP mode inactive")
                    
                    self.last_state = current_state.copy()
                
                # Sleep until next check
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                # Continue running even if there's an error
                time.sleep(self.check_interval)
        
        logger.info("WiFi Monitor Daemon stopped")
        
        # Ensure AP mode is disabled on shutdown if WiFi or Ethernet is connected
        try:
            status = self.wifi_manager.get_wifi_status()
            ethernet_connected = self.wifi_manager._is_ethernet_connected()
            if (status.connected or ethernet_connected) and status.ap_mode_active:
                if status.connected:
                    logger.info("Disabling AP mode on shutdown (WiFi is connected)")
                elif ethernet_connected:
                    logger.info("Disabling AP mode on shutdown (Ethernet is connected)")
                self.wifi_manager.disable_ap_mode()
        except Exception as e:
            logger.error(f"Error disabling AP mode on shutdown: {e}")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='WiFi Monitor Daemon for LED Matrix')
    parser.add_argument(
        '--interval',
        type=int,
        default=30,
        help='Check interval in seconds (default: 30)'
    )
    parser.add_argument(
        '--foreground',
        action='store_true',
        help='Run in foreground (for debugging)'
    )
    
    args = parser.parse_args()
    
    daemon = WiFiMonitorDaemon(check_interval=args.interval)
    
    try:
        daemon.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()

