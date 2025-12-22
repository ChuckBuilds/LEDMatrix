"""
WiFi Manager for Raspberry Pi LED Matrix

Handles WiFi connection management, access point mode, and network scanning.
Only enables AP mode when there is no active WiFi connection.
"""

import subprocess
import json
import logging
import os
import time
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Path for storing WiFi configuration (will be set dynamically)
# Default location, can be overridden
def get_wifi_config_path():
    """Get the WiFi configuration file path dynamically"""
    # Try to determine project root
    project_root = os.environ.get('LEDMATRIX_ROOT')
    if not project_root:
        # Try to find project root by looking for config directory
        current = Path(__file__).resolve().parent.parent
        if (current / 'config').exists():
            project_root = str(current)
        else:
            # Fallback to common location
            project_root = "/home/ledpi/LEDMatrix"
    
    return Path(project_root) / "config" / "wifi_config.json"

HOSTAPD_CONFIG_PATH = Path("/etc/hostapd/hostapd.conf")
DNSMASQ_CONFIG_PATH = Path("/etc/dnsmasq.conf")
HOSTAPD_SERVICE = "hostapd"
DNSMASQ_SERVICE = "dnsmasq"

# Default AP settings
DEFAULT_AP_SSID = "LEDMatrix-Setup"
DEFAULT_AP_PASSWORD = "ledmatrix123"
DEFAULT_AP_CHANNEL = 7

# LED status message file (for display_controller integration)
LED_STATUS_FILE = None  # Will be set dynamically


@dataclass
class WiFiNetwork:
    """Represents a WiFi network"""
    ssid: str
    signal: int
    security: str  # 'open', 'wpa', 'wpa2', 'wpa3'
    frequency: float = 0.0
    bssid: str = ""


@dataclass
class WiFiStatus:
    """Current WiFi connection status"""
    connected: bool
    ssid: Optional[str] = None
    ip_address: Optional[str] = None
    signal: int = 0
    ap_mode_active: bool = False


class WiFiManager:
    """Manages WiFi connections and access point mode"""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize WiFi Manager
        
        Args:
            config_path: Path to WiFi configuration file (defaults to project config directory)
        """
        if config_path is None:
            self.config_path = get_wifi_config_path()
        else:
            self.config_path = config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_config()
        
        # Set LED status file path (for display_controller integration)
        global LED_STATUS_FILE
        if LED_STATUS_FILE is None:
            project_root = self.config_path.parent.parent
            LED_STATUS_FILE = project_root / "config" / "wifi_status.json"
        
        # Check which tools are available
        self.has_nmcli = self._check_command("nmcli")
        self.has_iwlist = self._check_command("iwlist")
        self.has_hostapd = self._check_command("hostapd")
        self.has_dnsmasq = self._check_command("dnsmasq")
        
        # Initialize disconnected check counter for grace period
        # This prevents AP mode from enabling on transient network hiccups
        self._disconnected_checks = 0
        self._disconnected_checks_required = 3  # Require 3 consecutive disconnected checks (90 seconds at 30s interval)
        
        logger.info(f"WiFi Manager initialized - nmcli: {self.has_nmcli}, iwlist: {self.has_iwlist}, "
                   f"hostapd: {self.has_hostapd}, dnsmasq: {self.has_dnsmasq}")
    
    def _show_led_message(self, message: str, duration: int = 5):
        """
        Show a WiFi status message on the LED display.
        Writes to a JSON file that display_controller can read.
        
        Args:
            message: Text to display
            duration: How long to show message (seconds)
        """
        try:
            if LED_STATUS_FILE is None:
                return
            
            status = {
                'message': message,
                'timestamp': time.time(),
                'duration': duration
            }
            LED_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LED_STATUS_FILE, 'w') as f:
                json.dump(status, f)
            logger.info(f"LED message: {message}")
        except Exception as e:
            logger.debug(f"Could not write LED status message: {e}")
    
    def _clear_led_message(self):
        """Clear any WiFi status message from LED display."""
        try:
            if LED_STATUS_FILE and LED_STATUS_FILE.exists():
                LED_STATUS_FILE.unlink()
        except Exception as e:
            logger.debug(f"Could not clear LED status message: {e}")
    
    def _check_command(self, command: str) -> bool:
        """Check if a command is available"""
        try:
            # First try 'which' command
            result = subprocess.run(
                ["which", command],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                return True
            
            # Check common sbin paths (not in standard user PATH)
            sbin_paths = [
                f"/usr/sbin/{command}",
                f"/sbin/{command}",
                f"/usr/local/sbin/{command}"
            ]
            for path in sbin_paths:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return True
            
            return False
        except:
            return False
    
    def _load_config(self):
        """Load WiFi configuration from file"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    self.config = json.load(f)
                logger.info(f"Loaded WiFi config from {self.config_path}")
            except Exception as e:
                logger.warning(f"Failed to load WiFi config: {e}")
                self.config = {}
        else:
            self.config = {
                "ap_ssid": DEFAULT_AP_SSID,
                "ap_password": DEFAULT_AP_PASSWORD,
                "ap_channel": DEFAULT_AP_CHANNEL,
                "auto_enable_ap_mode": True,  # Default: auto-enable when no network (safe due to grace period)
                "saved_networks": []
            }
            self._save_config()
        
        # Ensure auto_enable_ap_mode exists in config (for existing configs)
        if "auto_enable_ap_mode" not in self.config:
            self.config["auto_enable_ap_mode"] = True  # Default: auto-enable when no network (safe due to grace period)
            self._save_config()
    
    def _save_config(self):
        """Save WiFi configuration to file"""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info(f"Saved WiFi config to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save WiFi config: {e}")
    
    def get_wifi_status(self) -> WiFiStatus:
        """
        Get current WiFi connection status
        
        Returns:
            WiFiStatus object with connection information
        """
        try:
            if self.has_nmcli:
                return self._get_status_nmcli()
            else:
                return self._get_status_iwconfig()
        except Exception as e:
            logger.error(f"Error getting WiFi status: {e}")
            return WiFiStatus(connected=False)
    
    def _get_status_nmcli(self) -> WiFiStatus:
        """Get WiFi status using nmcli"""
        try:
            # Check if connected - use device status first (more reliable)
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.warning("nmcli device status failed, assuming disconnected")
                return WiFiStatus(connected=False)
            
            wifi_connected = False
            ssid = None
            ip_address = None
            signal = 0
            wlan_device = None
            
            # Find WiFi device and check its state
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split(':')
                if len(parts) >= 3:
                    device = parts[0].strip()
                    dev_type = parts[1].strip().lower()
                    state = parts[2].strip().lower()
                    
                    # Check if it's a WiFi device
                    if dev_type == "wifi" or device.startswith("wlan"):
                        wlan_device = device
                        if state == "connected":
                            wifi_connected = True
                            break
                        elif state in ["disconnected", "unavailable", "unmanaged"]:
                            # Explicitly disconnected
                            wifi_connected = False
                            break
            
            # Get actual SSID and signal strength from WiFi device if connected
            # Use device show to get the real SSID and signal, not the connection name
            if wifi_connected and wlan_device:
                # Get both SSID and signal in one query for efficiency
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "802-11-wireless.ssid,WIFI.SIGNAL", "device", "show", wlan_device],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if '802-11-wireless.ssid:' in line:
                            ssid = line.split(':', 1)[1].strip()
                            if ssid:
                                continue
                        elif 'WIFI.SIGNAL:' in line:
                            try:
                                signal = int(line.split(':', 1)[1].strip())
                            except (ValueError, IndexError):
                                pass
                
                # Fallback: Get SSID from active WiFi connection list if not found
                if not ssid:
                    result = subprocess.run(
                        ["nmcli", "-t", "-f", "active,ssid", "device", "wifi"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            parts = line.split(':')
                            if len(parts) >= 2 and parts[0].strip() == "yes":
                                ssid = parts[1].strip()
                                if ssid:
                                    break
                
                # Fallback: Get signal strength if not already retrieved
                if signal == 0 and wlan_device:
                    result = subprocess.run(
                        ["nmcli", "-t", "-f", "WIFI.SIGNAL", "device", "show", wlan_device],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if 'WIFI.SIGNAL:' in line:
                                try:
                                    signal = int(line.split(':', 1)[1].strip())
                                    break
                                except (ValueError, IndexError):
                                    pass
            
            # Get IP address if connected
            if wifi_connected and wlan_device:
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", wlan_device],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if '/' in line:
                            ip_address = line.split('/')[0].strip()
                            break
                
                # Final fallback: Get signal strength by matching SSID in WiFi list
                # (Only if we still don't have signal from device properties)
                if signal == 0 and ssid:
                    result = subprocess.run(
                        ["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            parts = line.split(':')
                            if len(parts) >= 2:
                                line_ssid = parts[0].strip()
                                if line_ssid == ssid:
                                    try:
                                        signal = int(parts[1].strip())
                                        break
                                    except (ValueError, IndexError):
                                        pass
            
            # Check if AP mode is active
            ap_active = self._is_ap_mode_active()
            
            return WiFiStatus(
                connected=wifi_connected,
                ssid=ssid,
                ip_address=ip_address,
                signal=signal,
                ap_mode_active=ap_active
            )
        except Exception as e:
            logger.error(f"Error getting status with nmcli: {e}")
            return WiFiStatus(connected=False)
    
    def _get_status_iwconfig(self) -> WiFiStatus:
        """Get WiFi status using iwconfig (fallback)"""
        try:
            result = subprocess.run(
                ["iwconfig", "wlan0"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return WiFiStatus(connected=False)
            
            output = result.stdout
            connected = "ESSID:" in output and "not-associated" not in output
            
            ssid = None
            if connected:
                match = re.search(r'ESSID:"([^"]+)"', output)
                if match:
                    ssid = match.group(1)
            
            # Get IP address
            ip_address = None
            if connected:
                result = subprocess.run(
                    ["hostname", "-I"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    ips = result.stdout.strip().split()
                    for ip in ips:
                        if not ip.startswith('192.168.4.1'):  # Exclude AP IP
                            ip_address = ip
                            break
            
            ap_active = self._is_ap_mode_active()
            
            return WiFiStatus(
                connected=connected,
                ssid=ssid,
                ip_address=ip_address,
                ap_mode_active=ap_active
            )
        except Exception as e:
            logger.error(f"Error getting status with iwconfig: {e}")
            return WiFiStatus(connected=False)
    
    def _is_ethernet_connected(self) -> bool:
        """
        Check if Ethernet connection is active
        
        Returns:
            True if Ethernet is connected and has an IP address
        """
        try:
            # Check for Ethernet interfaces (eth0, enp*, etc.)
            # First try nmcli if available
            if self.has_nmcli:
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        parts = line.split(':')
                        if len(parts) >= 3:
                            device = parts[0].strip()
                            dev_type = parts[1].strip().lower()
                            state = parts[2].strip().lower()
                            
                            # Check if it's an Ethernet interface and connected
                            if dev_type == "ethernet" and state == "connected":
                                # Verify it has an IP address
                                ip_result = subprocess.run(
                                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", device],
                                    capture_output=True,
                                    text=True,
                                    timeout=5
                                )
                                if ip_result.returncode == 0 and ip_result.stdout.strip():
                                    return True
            
            # Fallback: Check using ip command
            result = subprocess.run(
                ["ip", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Look for Ethernet interfaces (eth0, enp*, etc.)
                lines = result.stdout.split('\n')
                in_ethernet = False
                for line in lines:
                    # Check if line starts interface name (e.g., "2: eth0:")
                    if re.match(r'^\d+:\s+(eth\d+|enp\d+s\d+|enx[0-9a-f]+):', line):
                        in_ethernet = True
                    elif in_ethernet and 'inet ' in line and not '127.0.0.1' in line:
                        # Found an IP address on Ethernet interface
                        return True
                    elif re.match(r'^\d+:', line) and in_ethernet:
                        # Moved to next interface
                        in_ethernet = False
            
            return False
        except Exception as e:
            logger.debug(f"Error checking Ethernet connection: {e}")
            return False
    
    def _has_connectivity_safety(self) -> bool:
        """
        Check if there's a safe fallback connectivity option available.
        
        Returns True if either:
        - Ethernet is connected, OR
        - WiFi radio is enabled (even if not connected to a network)
        
        This helps prevent lockout scenarios where we might disable WiFi
        without having Ethernet as backup.
        
        Returns:
            True if there's a safe connectivity option available
        """
        try:
            # Check if Ethernet is connected (safest fallback)
            if self._is_ethernet_connected():
                return True
            
            # Check if WiFi radio is enabled (at least WiFi is available)
            result = subprocess.run(
                ["nmcli", "radio", "wifi"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                status = result.stdout.strip().lower()
                if status == "enabled":
                    return True
            
            return False
        except Exception as e:
            logger.debug(f"Error checking connectivity safety: {e}")
            # If we can't determine, assume unsafe to be conservative
            return False
    
    def _is_ap_mode_active(self) -> bool:
        """Check if access point mode is currently active"""
        try:
            # Check if hostapd is running (captive portal mode)
            result = subprocess.run(
                ["systemctl", "is-active", HOSTAPD_SERVICE],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.stdout.strip() == "active":
                return True
            
            # Check if nmcli hotspot is active (fallback mode)
            hotspot_status = self._get_ap_status_nmcli()
            if hotspot_status.get('active'):
                return True
            
            return False
        except:
            return False
    
    def scan_networks(self) -> List[WiFiNetwork]:
        """
        Scan for available WiFi networks
        
        If AP mode is active, it will be temporarily disabled during scanning
        and re-enabled afterward. This is necessary because WiFi interfaces
        in AP mode cannot scan for other networks.
        
        Returns:
            List of WiFiNetwork objects
        """
        ap_was_active = False
        try:
            # Check if AP mode is active - if so, we need to disable it temporarily
            ap_was_active = self._is_ap_mode_active()
            
            if ap_was_active:
                logger.info("AP mode is active, temporarily disabling for WiFi scan...")
                success, message = self.disable_ap_mode()
                if not success:
                    logger.warning(f"Failed to disable AP mode for scanning: {message}")
                    # Continue anyway - scan might still work
                else:
                    # Wait for interface to switch modes
                    time.sleep(3)
            
            # Perform the scan
            if self.has_nmcli:
                networks = self._scan_nmcli()
            elif self.has_iwlist:
                networks = self._scan_iwlist()
            else:
                logger.error("No WiFi scanning tools available")
                networks = []
            
            return networks
            
        except Exception as e:
            logger.error(f"Error scanning networks: {e}")
            return []
        finally:
            # Always try to restore AP mode if it was active before
            if ap_was_active:
                logger.info("Re-enabling AP mode after WiFi scan...")
                time.sleep(1)  # Brief delay before re-enabling
                success, message = self.enable_ap_mode()
                if success:
                    logger.info("AP mode re-enabled successfully after scan")
                else:
                    logger.warning(f"Failed to re-enable AP mode after scan: {message}")
                    # Log but don't fail - user can manually re-enable if needed
    
    def _scan_nmcli(self) -> List[WiFiNetwork]:
        """Scan networks using nmcli"""
        networks = []
        try:
            # Trigger scan
            subprocess.run(
                ["nmcli", "device", "wifi", "rescan"],
                capture_output=True,
                timeout=10
            )
            time.sleep(2)  # Wait for scan to complete
            
            # Get scan results
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "device", "wifi", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return []
            
            seen_ssids = set()
            for line in result.stdout.strip().split('\n'):
                if not line or ':' not in line:
                    continue
                
                parts = line.split(':')
                if len(parts) >= 3:
                    ssid = parts[0].strip()
                    if not ssid or ssid in seen_ssids:
                        continue
                    
                    seen_ssids.add(ssid)
                    
                    try:
                        signal = int(parts[1].strip())
                        security = parts[2].strip() if len(parts) > 2 else "open"
                        
                        # Parse frequency - strip " MHz" if present
                        frequency_str = parts[3].strip() if len(parts) > 3 else "0"
                        frequency_str = frequency_str.replace(" MHz", "").replace("MHz", "").strip()
                        frequency = float(frequency_str) if frequency_str else 0.0
                        
                        # Normalize security type
                        if "WPA3" in security:
                            sec_type = "wpa3"
                        elif "WPA2" in security:
                            sec_type = "wpa2"
                        elif "WPA" in security:
                            sec_type = "wpa"
                        else:
                            sec_type = "open"
                        
                        networks.append(WiFiNetwork(
                            ssid=ssid,
                            signal=signal,
                            security=sec_type,
                            frequency=frequency
                        ))
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Skipping network line due to parsing error: {line[:50]}... Error: {e}")
                        continue
            
            # Sort by signal strength
            networks.sort(key=lambda x: x.signal, reverse=True)
            return networks
        except Exception as e:
            logger.error(f"Error scanning with nmcli: {e}")
            return []
    
    def _scan_iwlist(self) -> List[WiFiNetwork]:
        """Scan networks using iwlist (fallback)"""
        networks = []
        try:
            result = subprocess.run(
                ["iwlist", "wlan0", "scan"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return []
            
            output = result.stdout
            seen_ssids = set()
            current_ssid = None
            current_signal = 0
            current_security = "open"
            
            for line in output.split('\n'):
                line = line.strip()
                
                # Extract SSID
                if 'ESSID:' in line:
                    match = re.search(r'ESSID:"([^"]+)"', line)
                    if match:
                        if current_ssid and current_ssid not in seen_ssids:
                            networks.append(WiFiNetwork(
                                ssid=current_ssid,
                                signal=current_signal,
                                security=current_security
                            ))
                            seen_ssids.add(current_ssid)
                        current_ssid = match.group(1)
                        current_signal = 0
                        current_security = "open"
                
                # Extract signal strength
                elif 'Signal level=' in line:
                    match = re.search(r'Signal level=(-?\d+)', line)
                    if match:
                        # Convert to percentage (approximate)
                        dbm = int(match.group(1))
                        current_signal = max(0, min(100, (dbm + 100) * 2))
                
                # Extract security
                elif 'Encryption key:' in line:
                    if 'on' in line.lower():
                        current_security = "wpa"  # Default, will check for WPA2/WPA3
                elif 'WPA2' in line:
                    current_security = "wpa2"
                elif 'WPA3' in line:
                    current_security = "wpa3"
            
            # Add last network
            if current_ssid and current_ssid not in seen_ssids:
                networks.append(WiFiNetwork(
                    ssid=current_ssid,
                    signal=current_signal,
                    security=current_security
                ))
            
            # Sort by signal strength
            networks.sort(key=lambda x: x.signal, reverse=True)
            return networks
        except Exception as e:
            logger.error(f"Error scanning with iwlist: {e}")
            return []
    
    def connect_to_network(self, ssid: str, password: str) -> Tuple[bool, str]:
        """
        Connect to a WiFi network with failsafe to restore original connection on failure.
        
        Args:
            ssid: Network SSID
            password: Network password (empty for open networks)
            
        Returns:
            Tuple of (success, message)
        """
        # Save current connection info for failsafe restoration
        original_connection = None
        original_ssid = None
        try:
            status = self.get_wifi_status()
            if status.connected and status.ssid:
                original_ssid = status.ssid
                # Get the active connection name/UUID for wlan0
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", "wlan0"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if 'GENERAL.CONNECTION:' in line:
                            connection_name = line.split(':', 1)[1].strip()
                            if connection_name and connection_name != '--':
                                original_connection = connection_name
                                break
                
                # Fallback: try to find connection by SSID
                if not original_connection:
                    result = subprocess.run(
                        ["nmcli", "-t", "-f", "NAME", "connection", "show"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if original_ssid.lower() in line.lower():
                                original_connection = line.strip()
                                break
                
                logger.info(f"Saving original connection for failsafe: {original_ssid} ({original_connection})")
        except Exception as e:
            logger.debug(f"Could not save original connection info: {e}")
        
        try:
            # Check if already connected to the target network
            if original_ssid and original_ssid == ssid:
                logger.info(f"Already connected to {ssid}, verifying connection...")
                status = self.get_wifi_status()
                if status.connected and status.ssid == ssid:
                    logger.info(f"Already connected to {ssid} with IP {status.ip_address}")
                    return True, f"Already connected to {ssid}"
                else:
                    logger.warning(f"Status shows not connected to {ssid}, attempting reconnection...")
            
            # First, disable AP mode if active
            self.disable_ap_mode()
            time.sleep(2)
            
            # If we're currently connected to a different network, disconnect first
            # This ensures a clean switch between networks
            if original_ssid and original_ssid != ssid:
                logger.info(f"Switching networks: disconnecting from {original_ssid} before connecting to {ssid}")
                self._show_led_message(f"Switching networks...", duration=3)
                # Skip AP mode check since we're about to connect to a new network
                disconnect_success, disconnect_msg = self.disconnect_from_network(skip_ap_check=True)
                if disconnect_success:
                    logger.info(f"Disconnected from {original_ssid}: {disconnect_msg}")
                    time.sleep(1)  # Brief pause after disconnect
                else:
                    logger.warning(f"Failed to disconnect from {original_ssid}: {disconnect_msg}")
                    # Continue anyway - NetworkManager might handle it
            
            # Ensure WiFi radio is enabled before attempting connection (safety measure)
            if not self._ensure_wifi_radio_enabled():
                logger.warning("WiFi radio enable check failed, but continuing with connection attempt")
            
            if self.has_nmcli:
                success, message = self._connect_nmcli(ssid, password)
                
                # If connection failed, try to restore original connection
                if not success and original_connection and original_ssid:
                    logger.warning(f"Connection to {ssid} failed, attempting to restore original connection: {original_ssid}")
                    self._show_led_message(f"Restoring {original_ssid}...", duration=5)
                    
                    restore_success = self._restore_original_connection(original_connection, original_ssid)
                    if restore_success:
                        logger.info(f"Successfully restored original connection: {original_ssid}")
                        self._show_led_message("Restored!", duration=3)
                        return False, f"Failed to connect to {ssid}, restored {original_ssid}"
                    else:
                        logger.error(f"Failed to restore original connection: {original_ssid}")
                        # Trigger AP mode as last resort
                        self._show_led_message("Enabling AP mode...", duration=5)
                        ap_success, ap_msg = self.enable_ap_mode()
                        if ap_success:
                            logger.info("AP mode enabled as failsafe")
                            return False, f"Connection failed and restoration failed. AP mode enabled."
                        else:
                            logger.error(f"Failed to enable AP mode: {ap_msg}")
                            return False, f"Connection failed, restoration failed, and AP mode failed: {ap_msg}"
                
                # If connection failed and no original connection to restore, enable AP mode
                elif not success:
                    logger.warning(f"Connection to {ssid} failed and no original connection to restore")
                    self._show_led_message("Enabling AP mode...", duration=5)
                    ap_success, ap_msg = self.enable_ap_mode()
                    if ap_success:
                        logger.info("AP mode enabled as failsafe")
                        return False, f"Connection failed. AP mode enabled."
                    else:
                        return False, f"Connection failed and AP mode failed: {ap_msg}"
                
                return success, message
            else:
                return self._connect_wpa_supplicant(ssid, password)
        except Exception as e:
            logger.error(f"Error connecting to network: {e}")
            # Try to restore original connection on exception
            if original_connection and original_ssid:
                try:
                    logger.warning(f"Exception during connection, attempting to restore: {original_ssid}")
                    self._restore_original_connection(original_connection, original_ssid)
                except Exception as restore_error:
                    logger.error(f"Failed to restore after exception: {restore_error}")
                    # Last resort: enable AP mode
                    try:
                        self.enable_ap_mode()
                    except Exception:
                        pass
            return False, str(e)
    
    def _restore_original_connection(self, connection_name: str, ssid: str) -> bool:
        """
        Restore a previously active WiFi connection.
        
        Args:
            connection_name: NetworkManager connection name or UUID
            ssid: SSID for verification
            
        Returns:
            True if restoration successful, False otherwise
        """
        try:
            logger.info(f"Attempting to restore connection: {connection_name} ({ssid})")
            
            # Try to activate the connection
            result = subprocess.run(
                ["nmcli", "connection", "up", connection_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Wait for connection to stabilize
                time.sleep(3)
                
                # Verify connection
                status = self.get_wifi_status()
                if status.connected:
                    # Double-check SSID matches (if we can get it)
                    if status.ssid:
                        if status.ssid == ssid:
                            logger.info(f"Successfully restored connection to {ssid}")
                            return True
                        else:
                            logger.warning(f"Restored connection but SSID mismatch: expected {ssid}, got {status.ssid}")
                            # Still consider it success if we're connected
                            return True
                    else:
                        # Connected but can't verify SSID - assume success
                        logger.info("Restored connection (SSID verification unavailable)")
                        return True
                else:
                    logger.warning("Connection activation succeeded but not connected")
                    return False
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Failed to restore connection {connection_name}: {error_msg}")
                return False
        except Exception as e:
            logger.error(f"Error restoring connection: {e}")
            return False
    
    def _connect_nmcli(self, ssid: str, password: str) -> Tuple[bool, str]:
        """Connect using nmcli"""
        try:
            # Show LED message
            self._show_led_message(f"Connecting to {ssid}...", duration=10)
            
            # First, check if connection already exists and try to activate it
            check_result = subprocess.run(
                ["nmcli", "connection", "show", ssid],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if check_result.returncode == 0:
                # Connection exists, try to activate it first (faster and more reliable)
                logger.info(f"Found existing connection for {ssid}, activating...")
                result = subprocess.run(
                    ["nmcli", "connection", "up", ssid],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    # Wait longer for connection to stabilize and verify multiple times
                    max_verification_attempts = 5
                    verification_delay = 2
                    connected = False
                    
                    for attempt in range(max_verification_attempts):
                        time.sleep(verification_delay)
                        status = self.get_wifi_status()
                        if status.connected and status.ssid == ssid:
                            connected = True
                            break
                    
                    if connected:
                        # Save network to config
                        self._save_network(ssid, password)
                        
                        ip = status.ip_address or "Unknown"
                        self._show_led_message(f"Connected! {ip}", duration=5)
                        logger.info(f"Successfully connected to {ssid} with IP {ip}")
                        return True, f"Connected to {ssid}"
                    else:
                        logger.warning(f"Connection activation succeeded but verification failed for {ssid}")
                        self._show_led_message("Verification failed", duration=5)
                        return False, "Connection activated but verification failed"
            
            # No existing connection or activation failed, create new connection
            logger.info(f"Creating new connection for {ssid}...")
            self._save_network(ssid, password)
            
            # Connect using nmcli
            if password:
                cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
            else:
                cmd = ["nmcli", "device", "wifi", "connect", ssid]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Wait longer for connection to stabilize and verify multiple times
                max_verification_attempts = 5
                verification_delay = 2
                connected = False
                
                for attempt in range(max_verification_attempts):
                    time.sleep(verification_delay)
                    status = self.get_wifi_status()
                    if status.connected:
                        # Verify we're connected to the correct SSID
                        if status.ssid == ssid:
                            connected = True
                            break
                        elif status.ssid:
                            # Connected to different network - this is a failure
                            logger.warning(f"Connected to wrong network: {status.ssid} instead of {ssid}")
                            break
                
                if connected:
                    ip = status.ip_address or "Unknown"
                    self._show_led_message(f"Connected! {ip}", duration=5)
                    logger.info(f"Successfully connected to {ssid} with IP {ip}")
                    return True, f"Connected to {ssid}"
                else:
                    self._show_led_message("Connection failed", duration=5)
                    return False, "Connection command succeeded but verification failed"
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Failed to connect to {ssid}: {error_msg}")
                self._show_led_message("Connection failed", duration=5)
                return False, error_msg
        except Exception as e:
            logger.error(f"Error connecting with nmcli: {e}")
            self._show_led_message("Connection error", duration=5)
            return False, str(e)
    
    def _connect_wpa_supplicant(self, ssid: str, password: str) -> Tuple[bool, str]:
        """Connect using wpa_supplicant (fallback)"""
        try:
            self._save_network(ssid, password)
            
            # This would require modifying /etc/wpa_supplicant/wpa_supplicant.conf
            # For now, return not implemented
            return False, "wpa_supplicant connection not yet implemented. Please use NetworkManager (nmcli)."
        except Exception as e:
            logger.error(f"Error connecting with wpa_supplicant: {e}")
            return False, str(e)
    
    def disconnect_from_network(self, skip_ap_check: bool = False) -> Tuple[bool, str]:
        """
        Disconnect from the current WiFi network
        
        Args:
            skip_ap_check: If True, skip auto-enabling AP mode after disconnect
                          (useful when switching networks)
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Check if WiFi is connected
            status = self.get_wifi_status()
            if not status.connected:
                return True, "Not connected to any WiFi network"
            
            # Disconnect using nmcli
            if self.has_nmcli:
                result = subprocess.run(
                    ["sudo", "nmcli", "device", "disconnect", "wlan0"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if result.returncode == 0:
                    logger.info("Successfully disconnected from WiFi network")
                    # Wait a moment for the disconnect to complete
                    time.sleep(1)
                    
                    # Check if AP mode should be auto-enabled
                    # Skip if we're switching networks (skip_ap_check=True)
                    if not skip_ap_check:
                        auto_enable = self.config.get("auto_enable_ap_mode", True)
                        if auto_enable:
                            # Give it a moment, then check if we should enable AP mode
                            time.sleep(1)
                            self.check_and_manage_ap_mode()
                    else:
                        logger.debug("Skipping AP mode check (network switch in progress)")
                    
                    return True, "Disconnected from WiFi network"
                else:
                    error_msg = result.stderr.strip() or result.stdout.strip()
                    logger.error(f"Failed to disconnect from WiFi: {error_msg}")
                    return False, f"Failed to disconnect: {error_msg}"
            else:
                return False, "nmcli is required to disconnect from WiFi"
        except Exception as e:
            logger.error(f"Error disconnecting from WiFi: {e}")
            return False, str(e)
    
    def _save_network(self, ssid: str, password: str):
        """Save network credentials to config"""
        # Remove existing entry for this SSID
        self.config["saved_networks"] = [
            n for n in self.config["saved_networks"]
            if n.get("ssid") != ssid
        ]
        
        # Add new entry
        self.config["saved_networks"].append({
            "ssid": ssid,
            "password": password,
            "saved_at": time.time()
        })
        
        self._save_config()
    
    def _ensure_wifi_radio_enabled(self, max_retries: int = 3) -> bool:
        """
        Ensure WiFi radio is enabled (not soft-blocked) with retry logic and verification.
        
        Args:
            max_retries: Maximum number of retry attempts to enable WiFi radio
            
        Returns:
            True if WiFi is enabled or was successfully enabled, False otherwise
        """
        for attempt in range(max_retries):
            try:
                # Check if WiFi radio is enabled
                result = subprocess.run(
                    ["nmcli", "radio", "wifi"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    status = result.stdout.strip().lower()
                    if status == "enabled":
                        # Verify with rfkill as well
                        rfkill_result = subprocess.run(
                            ["rfkill", "list", "wifi"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if "Soft blocked: yes" not in rfkill_result.stdout:
                            logger.debug(f"WiFi radio confirmed enabled (attempt {attempt + 1})")
                            return True
                        # If soft-blocked, continue to unblock logic below
                    
                    if status == "disabled" or attempt > 0:
                        # Try to enable WiFi radio
                        if attempt == 0:
                            logger.info("WiFi radio is disabled, attempting to enable...")
                        else:
                            logger.info(f"WiFi radio still disabled, retry {attempt + 1}/{max_retries}...")
                        
                        enable_result = subprocess.run(
                            ["sudo", "nmcli", "radio", "wifi", "on"],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if enable_result.returncode == 0:
                            # Also unblock via rfkill in case it's soft-blocked
                            subprocess.run(
                                ["sudo", "rfkill", "unblock", "wifi"],
                                capture_output=True,
                                timeout=5
                            )
                            # Wait longer for it to actually enable
                            time.sleep(2)
                            
                            # Verify it's actually enabled now
                            verify_result = subprocess.run(
                                ["nmcli", "radio", "wifi"],
                                capture_output=True,
                                text=True,
                                timeout=5
                            )
                            if verify_result.returncode == 0 and verify_result.stdout.strip().lower() == "enabled":
                                logger.info("WiFi radio enabled and verified successfully")
                                return True
                            elif attempt < max_retries - 1:
                                logger.warning(f"WiFi radio enable command succeeded but not verified, will retry...")
                                time.sleep(1)
                                continue
                        else:
                            logger.warning(f"Failed to enable WiFi radio: {enable_result.stderr}")
                            if attempt < max_retries - 1:
                                time.sleep(1)
                                continue
                            return False
                
                # Fallback: try rfkill
                rfkill_result = subprocess.run(
                    ["rfkill", "list", "wifi"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if "Soft blocked: yes" in rfkill_result.stdout:
                    logger.info("WiFi is soft-blocked, unblocking via rfkill...")
                    subprocess.run(
                        ["sudo", "rfkill", "unblock", "wifi"],
                        capture_output=True,
                        timeout=5
                    )
                    time.sleep(2)
                    # Verify unblock worked
                    verify_rfkill = subprocess.run(
                        ["rfkill", "list", "wifi"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if "Soft blocked: yes" not in verify_rfkill.stdout:
                        logger.info("WiFi unblocked via rfkill and verified")
                        return True
                    elif attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                
                # If we get here and haven't returned, assume enabled if we can't determine
                if attempt == 0:
                    logger.debug("Could not determine WiFi radio status, assuming enabled")
                    return True
                else:
                    time.sleep(1)
                    continue
                    
            except Exception as e:
                logger.warning(f"Could not check/enable WiFi radio (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                # On last attempt, assume enabled to avoid blocking operations
                return True
        
        logger.warning(f"Failed to enable WiFi radio after {max_retries} attempts")
        return False
    
    def enable_ap_mode(self) -> Tuple[bool, str]:
        """
        Enable access point mode
        
        Only enables AP mode if:
        - WiFi is NOT connected AND
        - Ethernet is NOT connected
        
        Tries hostapd/dnsmasq first (captive portal), falls back to nmcli hotspot if that fails.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Check if already in AP mode
            if self._is_ap_mode_active():
                return True, "AP mode already active"
            
            # Ensure WiFi radio is enabled
            if not self._ensure_wifi_radio_enabled():
                return False, "WiFi radio is disabled and could not be enabled"
            
            # Check if WiFi is connected
            status = self.get_wifi_status()
            if status.connected:
                return False, "Cannot enable AP mode while WiFi is connected"
            
            # Check if Ethernet is connected
            if self._is_ethernet_connected():
                return False, "Cannot enable AP mode while Ethernet is connected"
            
            # Try hostapd/dnsmasq first (captive portal mode)
            if self.has_hostapd and self.has_dnsmasq:
                result = self._enable_ap_mode_hostapd()
                if result[0]:
                    return result
            
            # Fallback to nmcli hotspot (simpler, no captive portal)
            if self.has_nmcli:
                logger.info("hostapd/dnsmasq failed or unavailable, trying nmcli hotspot fallback...")
                self._show_led_message("Setup Mode", duration=5)
                return self._enable_ap_mode_nmcli_hotspot()
            
            return False, "No WiFi tools available (nmcli, hostapd, or dnsmasq required)"
        except Exception as e:
            logger.error(f"Error in enable_ap_mode: {e}")
            return False, str(e)
    
    def _enable_ap_mode_hostapd(self) -> Tuple[bool, str]:
        """Enable AP mode using hostapd and dnsmasq (captive portal)"""
        try:
            
            # Create hostapd config
            self._create_hostapd_config()
            
            # Create dnsmasq config
            self._create_dnsmasq_config()
            
            # Set up wlan0 for AP mode
            try:
                # Disconnect from any existing WiFi network
                subprocess.run(
                    ["sudo", "nmcli", "device", "disconnect", "wlan0"],
                    capture_output=True,
                    timeout=10
                )
                
                # Set static IP for AP mode
                subprocess.run(
                    ["sudo", "ip", "addr", "flush", "dev", "wlan0"],
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    ["sudo", "ip", "addr", "add", "192.168.4.1/24", "dev", "wlan0"],
                    capture_output=True,
                    timeout=10
                )
                subprocess.run(
                    ["sudo", "ip", "link", "set", "wlan0", "up"],
                    capture_output=True,
                    timeout=10
                )
                logger.info("Configured wlan0 with IP 192.168.4.1 for AP mode")
            except Exception as e:
                logger.warning(f"Error setting up wlan0 IP: {e}")
            
            # Start services
            try:
                # Start hostapd first (it sets up the AP)
                result = subprocess.run(
                    ["sudo", "systemctl", "start", HOSTAPD_SERVICE],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                if result.returncode != 0:
                    return False, f"Failed to start hostapd: {result.stderr}"
                
                # Give hostapd time to initialize
                time.sleep(1)
                
                # Start dnsmasq
                result = subprocess.run(
                    ["sudo", "systemctl", "start", DNSMASQ_SERVICE],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode != 0:
                    # Stop hostapd if dnsmasq failed
                    subprocess.run(["sudo", "systemctl", "stop", HOSTAPD_SERVICE], timeout=5)
                    return False, f"Failed to start dnsmasq: {result.stderr}"
                
                # Set up iptables port forwarding: redirect port 80 to 5000
                # This makes the captive portal work on standard HTTP port
                try:
                    # Check if iptables is available
                    iptables_check = subprocess.run(
                        ["which", "iptables"],
                        capture_output=True,
                        timeout=2
                    )
                    
                    if iptables_check.returncode == 0:
                        # Enable IP forwarding (needed for NAT)
                        subprocess.run(
                            ["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"],
                            capture_output=True,
                            timeout=5
                        )
                        
                        # Add NAT rule to redirect port 80 to 5000 on wlan0
                        # First check if rule already exists
                        check_result = subprocess.run(
                            ["sudo", "iptables", "-t", "nat", "-C", "PREROUTING", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", "5000"],
                            capture_output=True,
                            timeout=5
                        )
                        
                        if check_result.returncode != 0:
                            # Rule doesn't exist, add it
                            subprocess.run(
                                ["sudo", "iptables", "-t", "nat", "-A", "PREROUTING", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", "5000"],
                                capture_output=True,
                                timeout=5
                            )
                            logger.info("Added iptables rule to redirect port 80 to 5000")
                        
                        # Also allow incoming connections on port 80
                        check_input = subprocess.run(
                            ["sudo", "iptables", "-C", "INPUT", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "ACCEPT"],
                            capture_output=True,
                            timeout=5
                        )
                        
                        if check_input.returncode != 0:
                            subprocess.run(
                                ["sudo", "iptables", "-A", "INPUT", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "ACCEPT"],
                                capture_output=True,
                                timeout=5
                            )
                    else:
                        logger.debug("iptables not available, port forwarding not set up")
                        logger.info("Note: Port 80 forwarding requires iptables. Users will need to access port 5000 directly.")
                except Exception as e:
                    logger.warning(f"Could not set up iptables port forwarding: {e}")
                    # Continue anyway - port 5000 will still work
                
                logger.info("AP mode enabled successfully")
                self._show_led_message("Setup Mode Active", duration=5)
                return True, "AP mode enabled"
            except Exception as e:
                logger.error(f"Error starting AP services: {e}")
                return False, str(e)
        except Exception as e:
            logger.error(f"Error enabling AP mode: {e}")
            return False, str(e)
    
    def _enable_ap_mode_nmcli_hotspot(self) -> Tuple[bool, str]:
        """
        Enable AP mode using nmcli hotspot (simpler fallback, no captive portal).
        This is a fallback when hostapd/dnsmasq is not available or fails.
        """
        try:
            # Stop any existing connection
            self.disconnect_from_network()
            time.sleep(1)
            
            # Delete any existing hotspot connections (more thorough cleanup)
            # First, list all connections to find any hotspot-related ones
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if ':' in line:
                        conn_name, conn_type = line.split(':', 1)
                        conn_name = conn_name.strip()
                        conn_type = conn_type.strip().lower()
                        # Delete if it's a hotspot or matches our known names
                        if conn_type == '802-11-wireless' or 'hotspot' in conn_name.lower() or conn_name in ["Hotspot", "LEDMatrix-Setup-AP", "TickerSetup-AP"]:
                            logger.info(f"Deleting existing connection: {conn_name}")
                            subprocess.run(
                                ["nmcli", "connection", "delete", conn_name],
                                capture_output=True,
                                timeout=10
                            )
            
            # Also explicitly delete known connection names
            for conn_name in ["Hotspot", "LEDMatrix-Setup-AP", "TickerSetup-AP"]:
                subprocess.run(
                    ["nmcli", "connection", "delete", conn_name],
                    capture_output=True,
                    timeout=10
                )
            
            # Get AP settings from config
            ap_ssid = self.config.get("ap_ssid", DEFAULT_AP_SSID)
            
            # Use nmcli hotspot command (simpler, works with Broadcom chips)
            # Open network (no password) for easy setup access
            logger.info(f"Creating open hotspot with nmcli: {ap_ssid} (no password)")
            cmd = [
                "nmcli", "device", "wifi", "hotspot",
                "ifname", "wlan0",
                "con-name", "LEDMatrix-Setup-AP",
                "ssid", ap_ssid,
                "band", "bg"  # 2.4GHz for maximum compatibility
                # No password parameter = open network
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Verify the connection was created as open (no password) and fix if needed
                time.sleep(2)  # Give it a moment to create
                verify_result = subprocess.run(
                    ["nmcli", "-t", "-f", "802-11-wireless-security.key-mgmt", "connection", "show", "LEDMatrix-Setup-AP"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if verify_result.returncode == 0:
                    key_mgmt = verify_result.stdout.strip()
                    if key_mgmt and key_mgmt != "none":
                        logger.warning(f"Hotspot has security enabled ({key_mgmt}), removing password to make it open...")
                        # Remove security settings to make it open
                        subprocess.run(
                            ["nmcli", "connection", "modify", "LEDMatrix-Setup-AP", "802-11-wireless-security.key-mgmt", "none"],
                            capture_output=True,
                            timeout=5
                        )
                        subprocess.run(
                            ["nmcli", "connection", "modify", "LEDMatrix-Setup-AP", "802-11-wireless-security.psk", ""],
                            capture_output=True,
                            timeout=5
                        )
                        # Restart the connection to apply changes
                        subprocess.run(
                            ["nmcli", "connection", "down", "LEDMatrix-Setup-AP"],
                            capture_output=True,
                            timeout=5
                        )
                        time.sleep(1)
                        subprocess.run(
                            ["nmcli", "connection", "up", "LEDMatrix-Setup-AP"],
                            capture_output=True,
                            timeout=10
                        )
                        logger.info("Removed password from hotspot connection - it should now be open")
                else:
                    logger.debug("Could not verify hotspot security settings")
                logger.info(f"AP mode started via nmcli hotspot: {ap_ssid}")
                time.sleep(2)
                
                # Verify hotspot is running
                status = self._get_ap_status_nmcli()
                if status.get('active'):
                    ip = status.get('ip', '192.168.4.1')
                    logger.info(f"AP mode confirmed active at {ip}")
                    self._show_led_message(f"Setup: {ip}", duration=5)
                    return True, f"AP mode enabled (hotspot mode) - Access at {ip}:5000"
                else:
                    logger.error("AP mode started but not verified")
                    return False, "AP mode started but verification failed"
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Failed to start AP mode via nmcli: {error_msg}")
                self._show_led_message("AP mode failed", duration=5)
                return False, f"Failed to start AP mode: {error_msg}"
                
        except Exception as e:
            logger.error(f"Error starting AP mode with nmcli hotspot: {e}")
            self._show_led_message("Setup mode error", duration=5)
            return False, str(e)
    
    def _get_ap_status_nmcli(self) -> Dict:
        """
        Get AP status using nmcli (for hotspot mode).
        
        Returns:
            Dict with AP status info
        """
        try:
            # Check if hotspot connection is active
            result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and 'hotspot' in parts[1].lower():
                    return {
                        'active': True,
                        'ssid': self.config.get("ap_ssid", DEFAULT_AP_SSID),
                        'ip': '192.168.4.1',  # nmcli hotspot uses this IP
                        'interface': parts[2] if len(parts) > 2 else "wlan0"
                    }
            
            return {'active': False}
            
        except Exception as e:
            logger.error(f"Error getting AP status with nmcli: {e}")
            return {'active': False}
    
    def disable_ap_mode(self) -> Tuple[bool, str]:
        """
        Disable access point mode
        
        Returns:
            Tuple of (success, message)
        """
        try:
            if not self._is_ap_mode_active():
                return True, "AP mode not active"
            
            # Check which AP mode is active and disable accordingly
            # First check if hostapd is running (captive portal mode)
            hostapd_active = False
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", HOSTAPD_SERVICE],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                hostapd_active = result.stdout.strip() == "active"
            except:
                pass
            
            # Stop services
            try:
                if hostapd_active:
                    # Disable hostapd/dnsmasq mode (captive portal)
                    subprocess.run(
                        ["sudo", "systemctl", "stop", HOSTAPD_SERVICE],
                        capture_output=True,
                        timeout=10
                    )
                    subprocess.run(
                        ["sudo", "systemctl", "stop", DNSMASQ_SERVICE],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    # Disable nmcli hotspot mode (fallback)
                    for conn_name in ["LEDMatrix-Setup-AP", "Hotspot", "TickerSetup-AP"]:
                        subprocess.run(
                            ["nmcli", "connection", "down", conn_name],
                            capture_output=True,
                            timeout=10
                        )
                        subprocess.run(
                            ["nmcli", "connection", "delete", conn_name],
                            capture_output=True,
                            timeout=10
                        )
                
                # Restore original dnsmasq config if backup exists (only for hostapd mode)
                if hostapd_active:
                    backup_path = f"{DNSMASQ_CONFIG_PATH}.backup"
                    if os.path.exists(backup_path):
                        subprocess.run(
                            ["sudo", "cp", backup_path, str(DNSMASQ_CONFIG_PATH)],
                            timeout=10
                        )
                        logger.info("Restored original dnsmasq config from backup")
                    else:
                        # No backup - clear the captive portal config
                        # Create a minimal config that won't interfere
                        minimal_config = "# dnsmasq config - restored to minimal\n"
                        with open("/tmp/dnsmasq.conf", 'w') as f:
                            f.write(minimal_config)
                        subprocess.run(
                            ["sudo", "cp", "/tmp/dnsmasq.conf", str(DNSMASQ_CONFIG_PATH)],
                            timeout=10
                        )
                        logger.info("Cleared dnsmasq captive portal config")
                
                # Remove iptables port forwarding rules and disable IP forwarding (only for hostapd mode)
                if hostapd_active:
                    try:
                        # Check if iptables is available
                        iptables_check = subprocess.run(
                            ["which", "iptables"],
                            capture_output=True,
                            timeout=2
                        )
                        
                        if iptables_check.returncode == 0:
                            # Remove NAT redirect rule
                            subprocess.run(
                                ["sudo", "iptables", "-t", "nat", "-D", "PREROUTING", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "REDIRECT", "--to-port", "5000"],
                                capture_output=True,
                                timeout=5
                            )
                            
                            # Remove INPUT rule
                            subprocess.run(
                                ["sudo", "iptables", "-D", "INPUT", "-i", "wlan0", "-p", "tcp", "--dport", "80", "-j", "ACCEPT"],
                                capture_output=True,
                                timeout=5
                            )
                            
                            logger.info("Removed iptables port forwarding rules")
                        else:
                            logger.debug("iptables not available, skipping rule removal")
                        
                        # Disable IP forwarding (restore to default client mode)
                        subprocess.run(
                            ["sudo", "sysctl", "-w", "net.ipv4.ip_forward=0"],
                            capture_output=True,
                            timeout=5
                        )
                        logger.info("Disabled IP forwarding")
                    except Exception as e:
                        logger.warning(f"Could not remove iptables rules or disable forwarding: {e}")
                        # Continue anyway
                    
                    # Clean up wlan0 IP configuration
                    subprocess.run(
                        ["sudo", "ip", "addr", "del", "192.168.4.1/24", "dev", "wlan0"],
                        capture_output=True,
                        timeout=10
                    )
                    
                    # Only restart NetworkManager if hostapd was active (needed for hostapd/dnsmasq cleanup)
                    # Before restarting, ensure we have connectivity safety (Ethernet or WiFi enabled)
                    connectivity_safe = self._has_connectivity_safety()
                    
                    if not connectivity_safe:
                        # Ensure WiFi radio is enabled before restart to maintain connectivity option
                        logger.warning("No connectivity safety detected (no Ethernet, WiFi may be disabled), ensuring WiFi radio enabled before restart")
                        self._ensure_wifi_radio_enabled()
                    
                    logger.info("Restarting NetworkManager to restore normal WiFi operation after hostapd cleanup")
                    subprocess.run(
                        ["sudo", "systemctl", "restart", "NetworkManager"],
                        capture_output=True,
                        timeout=15
                    )
                    # Give NetworkManager time to restart
                    time.sleep(2)
                    
                    # Explicitly ensure WiFi radio is enabled after restart (with retries for safety)
                    wifi_enabled = self._ensure_wifi_radio_enabled(max_retries=5)
                    if not wifi_enabled:
                        logger.warning("WiFi radio may be disabled after NetworkManager restart - this could cause lockout if Ethernet not connected")
                        # Try one more time with rfkill as last resort
                        try:
                            subprocess.run(
                                ["sudo", "rfkill", "unblock", "wifi"],
                                capture_output=True,
                                timeout=5
                            )
                            time.sleep(1)
                            logger.info("Attempted final WiFi radio unblock via rfkill")
                        except Exception as e:
                            logger.error(f"Final WiFi radio unblock attempt failed: {e}")
                else:
                    # nmcli hotspot mode - restart not needed, just ensure WiFi radio is enabled
                    logger.info("Skipping NetworkManager restart (nmcli hotspot mode, restart not needed)")
                    # Still ensure WiFi radio is enabled (may have been disabled by nmcli operations)
                    # Use retries for safety
                    wifi_enabled = self._ensure_wifi_radio_enabled(max_retries=3)
                    if not wifi_enabled:
                        logger.warning("WiFi radio may be disabled after nmcli hotspot cleanup")
                
                logger.info("AP mode disabled successfully")
                return True, "AP mode disabled"
            except Exception as e:
                logger.error(f"Error stopping AP services: {e}")
                return False, str(e)
        except Exception as e:
            logger.error(f"Error disabling AP mode: {e}")
            return False, str(e)
    
    def _create_hostapd_config(self):
        """Create hostapd configuration file"""
        try:
            config_dir = HOSTAPD_CONFIG_PATH.parent
            config_dir.mkdir(parents=True, exist_ok=True)
            
            ap_ssid = self.config.get("ap_ssid", DEFAULT_AP_SSID)
            ap_channel = self.config.get("ap_channel", DEFAULT_AP_CHANNEL)
            
            # Open network configuration (no password) for easy setup access
            config_content = f"""interface=wlan0
driver=nl80211
ssid={ap_ssid}
hw_mode=g
channel={ap_channel}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
# Open network - no WPA/WPA2 encryption
"""
            
            # Write config (requires sudo)
            with open("/tmp/hostapd.conf", 'w') as f:
                f.write(config_content)
            
            # Copy to final location with sudo
            subprocess.run(
                ["sudo", "cp", "/tmp/hostapd.conf", str(HOSTAPD_CONFIG_PATH)],
                timeout=10
            )
            
            logger.info(f"Created hostapd config at {HOSTAPD_CONFIG_PATH}")
        except Exception as e:
            logger.error(f"Error creating hostapd config: {e}")
            raise
    
    def _create_dnsmasq_config(self):
        """Create dnsmasq configuration file with captive portal DNS redirection"""
        try:
            # Backup existing config
            if DNSMASQ_CONFIG_PATH.exists():
                subprocess.run(
                    ["sudo", "cp", str(DNSMASQ_CONFIG_PATH), f"{DNSMASQ_CONFIG_PATH}.backup"],
                    timeout=10
                )
            
            config_content = """interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h

# Captive portal: Redirect all DNS queries to Pi
address=/#/192.168.4.1

# Captive portal detection endpoints
address=/captive.apple.com/192.168.4.1
address=/connectivitycheck.gstatic.com/192.168.4.1
address=/www.msftconnecttest.com/192.168.4.1
address=/detectportal.firefox.com/192.168.4.1
"""
            
            # Write config (requires sudo)
            with open("/tmp/dnsmasq.conf", 'w') as f:
                f.write(config_content)
            
            # Copy to final location with sudo
            subprocess.run(
                ["sudo", "cp", "/tmp/dnsmasq.conf", str(DNSMASQ_CONFIG_PATH)],
                timeout=10
            )
            
            logger.info(f"Created dnsmasq config at {DNSMASQ_CONFIG_PATH} with captive portal DNS redirection")
        except Exception as e:
            logger.error(f"Error creating dnsmasq config: {e}")
            raise
    
    def check_and_manage_ap_mode(self) -> bool:
        """
        Check WiFi and Ethernet connection status and enable/disable AP mode accordingly.
        Only auto-enables AP mode if:
        - auto_enable_ap_mode is enabled in config AND
        - WiFi is NOT connected AND
        - Ethernet is NOT connected AND
        - Multiple consecutive disconnected checks (grace period to avoid false positives)
        
        Always auto-disables AP mode when WiFi or Ethernet connects.
        
        This should be called periodically by a background service.
        
        Returns:
            True if AP mode state changed, False otherwise
        """
        try:
            # Get status with retry for more reliable detection
            status = self._get_wifi_status_with_retry()
            ethernet_connected = self._is_ethernet_connected()
            ap_active = self._is_ap_mode_active()
            auto_enable = self.config.get("auto_enable_ap_mode", True)  # Default: True (safe due to grace period)
            
            # Log current state for debugging
            logger.debug(f"WiFi status: connected={status.connected}, SSID={status.ssid}, "
                        f"Ethernet={ethernet_connected}, AP_active={ap_active}, "
                        f"auto_enable={auto_enable}, disconnected_checks={self._disconnected_checks}")
            
            # Determine if we should have AP mode active
            # AP mode should only be auto-enabled if:
            # - auto_enable_ap_mode is True AND
            # - WiFi is NOT connected AND
            # - Ethernet is NOT connected AND
            # - We've had multiple consecutive disconnected checks (grace period)
            is_disconnected = not status.connected and not ethernet_connected
            
            if is_disconnected:
                # Increment disconnected check counter
                self._disconnected_checks += 1
                logger.debug(f"Network disconnected (check {self._disconnected_checks}/{self._disconnected_checks_required})")
            else:
                # Reset counter if we're connected
                if self._disconnected_checks > 0:
                    logger.debug(f"Network connected, resetting disconnected check counter")
                self._disconnected_checks = 0
            
            # Only enable AP if we've had enough consecutive disconnected checks
            should_have_ap = (auto_enable and 
                            is_disconnected and 
                            self._disconnected_checks >= self._disconnected_checks_required)
            
            if should_have_ap and not ap_active:
                # Should have AP but don't - enable AP mode (only if auto-enable is on and grace period passed)
                logger.info(f"Enabling AP mode after {self._disconnected_checks} consecutive disconnected checks")
                success, message = self.enable_ap_mode()
                if success:
                    logger.info("Auto-enabled AP mode (no WiFi or Ethernet connection after grace period)")
                    self._disconnected_checks = 0  # Reset counter after enabling
                    return True
                else:
                    logger.warning(f"Failed to enable AP mode: {message}")
            elif not should_have_ap and ap_active:
                # Should not have AP but do - disable AP mode
                # Always disable if WiFi or Ethernet connects, regardless of auto_enable setting
                if status.connected or ethernet_connected:
                    success, message = self.disable_ap_mode()
                    if success:
                        if status.connected:
                            logger.info("Auto-disabled AP mode (WiFi connected)")
                        elif ethernet_connected:
                            logger.info("Auto-disabled AP mode (Ethernet connected)")
                        self._disconnected_checks = 0  # Reset counter
                        return True
                    else:
                        logger.warning(f"Failed to auto-disable AP mode: {message}")
                elif not auto_enable:
                    # AP is active but auto_enable is disabled - this means it was manually enabled
                    # Don't disable it automatically, let it stay active
                    logger.debug("AP mode is active (manually enabled), keeping active")
            
            return False
        except Exception as e:
            logger.error(f"Error checking AP mode: {e}", exc_info=True)
            return False
    
    def _get_wifi_status_with_retry(self, max_retries=2) -> WiFiStatus:
        """
        Get WiFi status with retry logic to avoid false negatives.
        
        Args:
            max_retries: Number of retry attempts if first check fails
            
        Returns:
            WiFiStatus object
        """
        for attempt in range(max_retries + 1):
            status = self.get_wifi_status()
            # If we get a connected status, trust it immediately
            if status.connected:
                return status
            
            # If disconnected, wait a bit and retry (in case of transient issues)
            if attempt < max_retries:
                time.sleep(1)
                logger.debug(f"WiFi status check attempt {attempt + 1}/{max_retries + 1}: disconnected, retrying...")
        
        # Return the last status (disconnected)
        return status

