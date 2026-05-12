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
import subprocess
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
        # Counts consecutive checks where nmcli says "connected" but internet is unreachable.
        # After _nm_restart_threshold failures, NetworkManager is restarted as a recovery step.
        self._consecutive_internet_failures = 0
        self._nm_restart_threshold = 5  # ~2.5 min at 30s interval

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
        
        # Log initial configuration
        auto_enable = self.wifi_manager.config.get("auto_enable_ap_mode", True)
        ap_ssid = self.wifi_manager.config.get("ap_ssid", "LEDMatrix-Setup")
        logger.info(f"Configuration: auto_enable_ap_mode={auto_enable}, ap_ssid={ap_ssid}")
        
        # Log initial status
        initial_status = self.wifi_manager.get_wifi_status()
        initial_ethernet = self.wifi_manager._is_ethernet_connected()
        logger.info(f"Initial status: WiFi connected={initial_status.connected}, "
                   f"Ethernet connected={initial_ethernet}, AP active={initial_status.ap_mode_active}")
        if initial_status.connected:
            logger.info(f"  WiFi SSID: {initial_status.ssid}, IP: {initial_status.ip_address}, Signal: {initial_status.signal}%")
        
        while self.running:
            try:
                # Get current status before checking
                status = self.wifi_manager.get_wifi_status()
                ethernet_connected = self.wifi_manager._is_ethernet_connected()
                
                # Check WiFi status and manage AP mode
                state_changed = self.wifi_manager.check_and_manage_ap_mode()
                
                # Get updated status after check
                updated_status = self.wifi_manager.get_wifi_status()
                updated_ethernet = self.wifi_manager._is_ethernet_connected()
                
                current_state = {
                    'connected': updated_status.connected,
                    'ethernet_connected': updated_ethernet,
                    'ap_active': updated_status.ap_mode_active,
                    'ssid': updated_status.ssid
                }
                
                # Log state changes with detailed information
                if current_state != self.last_state:
                    logger.info("=== State Change Detected ===")
                    if updated_status.connected:
                        logger.info(f"WiFi connected: {updated_status.ssid} (IP: {updated_status.ip_address}, Signal: {updated_status.signal}%)")
                    else:
                        logger.info("WiFi disconnected (no active connection)")
                    
                    if updated_ethernet:
                        logger.info("Ethernet connected")
                    else:
                        logger.debug("Ethernet not connected")
                    
                    if updated_status.ap_mode_active:
                        logger.info(f"AP mode ACTIVE - SSID: {ap_ssid} (IP: 192.168.4.1)")
                    else:
                        logger.debug("AP mode inactive")
                    
                    if state_changed:
                        logger.info("AP mode state was changed by check_and_manage_ap_mode()")
                    
                    logger.info("=============================")
                    self.last_state = current_state.copy()
                else:
                    # Log periodic status (less verbose)
                    if updated_status.connected:
                        logger.debug(f"Status check: WiFi={updated_status.ssid} ({updated_status.signal}%), "
                                   f"Ethernet={updated_ethernet}, AP={updated_status.ap_mode_active}")
                    else:
                        logger.debug(f"Status check: WiFi=disconnected, Ethernet={updated_ethernet}, AP={updated_status.ap_mode_active}")
                
                # Escalating recovery: if nmcli reports connected but actual internet
                # is unreachable for several consecutive checks, restart NetworkManager.
                # This is done HERE (not inside check_and_manage_ap_mode) to keep the
                # AP-enable trigger clean and avoid false-positive AP enables from
                # transient packet loss on otherwise working WiFi.
                if updated_status.connected and not updated_status.ap_mode_active:
                    if not self.wifi_manager.check_internet_connectivity():
                        self._consecutive_internet_failures += 1
                        logger.warning(
                            f"Internet unreachable despite nmcli connection "
                            f"({self._consecutive_internet_failures}/{self._nm_restart_threshold})"
                        )
                        if self._consecutive_internet_failures >= self._nm_restart_threshold:
                            logger.warning("Restarting NetworkManager to recover internet connectivity")
                            try:
                                subprocess.run(
                                    ["/usr/bin/systemctl", "restart", "NetworkManager"],
                                    capture_output=True, timeout=20, check=True
                                )
                                self._consecutive_internet_failures = 0
                                # NM restart causes a brief WiFi drop; reset the AP-mode grace
                                # counter so that transient disconnect doesn't count toward
                                # triggering AP mode.
                                self.wifi_manager._disconnected_checks = 0
                            except subprocess.CalledProcessError as e:
                                logger.error(f"NetworkManager restart failed (rc={e.returncode}); "
                                             "resetting failure counter to avoid tight retry loop")
                                self._consecutive_internet_failures = 0
                            except Exception as e:
                                logger.error(f"NetworkManager restart error: {e}; "
                                             "resetting failure counter to avoid tight retry loop")
                                self._consecutive_internet_failures = 0
                    else:
                        self._consecutive_internet_failures = 0
                else:
                    self._consecutive_internet_failures = 0

                # Sleep until next check
                time.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                logger.error(f"Error details - type: {type(e).__name__}, args: {e.args}")
                # Log current state for debugging
                try:
                    error_status = self.wifi_manager.get_wifi_status()
                    logger.error(f"State at error: WiFi={error_status.connected}, AP={error_status.ap_mode_active}")
                except Exception as state_error:
                    logger.error(f"Could not get state at error: {state_error}")
                # Continue running even if there's an error
                time.sleep(self.check_interval)
        
        logger.info("WiFi Monitor Daemon stopped")
        
        # Ensure AP mode is disabled on shutdown if WiFi or Ethernet is connected
        logger.info("Performing cleanup on shutdown...")
        try:
            status = self.wifi_manager.get_wifi_status()
            ethernet_connected = self.wifi_manager._is_ethernet_connected()
            logger.info(f"Final status: WiFi={status.connected}, Ethernet={ethernet_connected}, AP={status.ap_mode_active}")
            
            if (status.connected or ethernet_connected) and status.ap_mode_active:
                if status.connected:
                    logger.info(f"Disabling AP mode on shutdown (WiFi is connected to {status.ssid})")
                elif ethernet_connected:
                    logger.info("Disabling AP mode on shutdown (Ethernet is connected)")
                
                success, message = self.wifi_manager.disable_ap_mode()
                if success:
                    logger.info(f"AP mode disabled successfully: {message}")
                else:
                    logger.warning(f"Failed to disable AP mode: {message}")
            else:
                logger.debug("AP mode cleanup not needed (not active or no network connection)")
        except Exception as e:
            logger.error(f"Error during shutdown cleanup: {e}", exc_info=True)


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

