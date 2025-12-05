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
        
        # Check which tools are available
        self.has_nmcli = self._check_command("nmcli")
        self.has_iwlist = self._check_command("iwlist")
        self.has_hostapd = self._check_command("hostapd")
        self.has_dnsmasq = self._check_command("dnsmasq")
        
        logger.info(f"WiFi Manager initialized - nmcli: {self.has_nmcli}, iwlist: {self.has_iwlist}, "
                   f"hostapd: {self.has_hostapd}, dnsmasq: {self.has_dnsmasq}")
    
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
                "auto_enable_ap_mode": True,  # Default: auto-enable when no network connection
                "saved_networks": []
            }
            self._save_config()
        
        # Ensure auto_enable_ap_mode exists in config (for existing configs)
        if "auto_enable_ap_mode" not in self.config:
            self.config["auto_enable_ap_mode"] = True  # Default: auto-enable when no network connection
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
            # Check if connected
            result = subprocess.run(
                ["nmcli", "-t", "-f", "STATE,CONNECTION", "device", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return WiFiStatus(connected=False)
            
            wifi_connected = False
            ssid = None
            ip_address = None
            signal = 0
            
            for line in result.stdout.strip().split('\n'):
                if 'wifi' in line.lower() or 'wlan' in line.lower():
                    parts = line.split(':')
                    if len(parts) >= 2:
                        state = parts[0].strip()
                        connection = parts[1].strip() if len(parts) > 1 else ""
                        
                        if state == "connected":
                            wifi_connected = True
                            ssid = connection
                            break
            
            # Get IP address if connected
            if wifi_connected:
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if '/' in line:
                            ip_address = line.split('/')[0].strip()
                            break
                
                # Get signal strength
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "SIGNAL", "device", "wifi"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if ssid and ssid in line:
                            parts = line.split(':')
                            if len(parts) >= 2:
                                try:
                                    signal = int(parts[-1].strip())
                                    break
                                except:
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
    
    def _is_ap_mode_active(self) -> bool:
        """Check if access point mode is currently active"""
        try:
            # Check if hostapd is running
            result = subprocess.run(
                ["systemctl", "is-active", HOSTAPD_SERVICE],
                capture_output=True,
                text=True,
                timeout=2
            )
            return result.stdout.strip() == "active"
        except:
            return False
    
    def scan_networks(self) -> List[WiFiNetwork]:
        """
        Scan for available WiFi networks
        
        Returns:
            List of WiFiNetwork objects
        """
        try:
            if self.has_nmcli:
                return self._scan_nmcli()
            elif self.has_iwlist:
                return self._scan_iwlist()
            else:
                logger.error("No WiFi scanning tools available")
                return []
        except Exception as e:
            logger.error(f"Error scanning networks: {e}")
            return []
    
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
                        frequency = float(parts[3].strip()) if len(parts) > 3 else 0.0
                        
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
                    except ValueError:
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
        Connect to a WiFi network
        
        Args:
            ssid: Network SSID
            password: Network password (empty for open networks)
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # First, disable AP mode if active
            self.disable_ap_mode()
            time.sleep(2)
            
            if self.has_nmcli:
                return self._connect_nmcli(ssid, password)
            else:
                return self._connect_wpa_supplicant(ssid, password)
        except Exception as e:
            logger.error(f"Error connecting to network: {e}")
            return False, str(e)
    
    def _connect_nmcli(self, ssid: str, password: str) -> Tuple[bool, str]:
        """Connect using nmcli"""
        try:
            # Save network to config
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
                logger.info(f"Successfully connected to {ssid}")
                return True, f"Connected to {ssid}"
            else:
                error_msg = result.stderr.strip() or result.stdout.strip()
                logger.error(f"Failed to connect to {ssid}: {error_msg}")
                return False, error_msg
        except Exception as e:
            logger.error(f"Error connecting with nmcli: {e}")
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
    
    def _ensure_wifi_radio_enabled(self) -> bool:
        """
        Ensure WiFi radio is enabled (not soft-blocked)
        
        Returns:
            True if WiFi is enabled or was successfully enabled, False otherwise
        """
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
                    return True
                elif status == "disabled":
                    # Try to enable WiFi radio
                    logger.info("WiFi radio is disabled, attempting to enable...")
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
                        time.sleep(1)  # Give it a moment to enable
                        logger.info("WiFi radio enabled successfully")
                        return True
                    else:
                        logger.error(f"Failed to enable WiFi radio: {enable_result.stderr}")
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
                time.sleep(1)
                logger.info("WiFi unblocked via rfkill")
                return True
            
            return True  # Assume enabled if we can't determine
        except Exception as e:
            logger.warning(f"Could not check/enable WiFi radio: {e}")
            return True  # Continue anyway, hostapd might still work
    
    def enable_ap_mode(self) -> Tuple[bool, str]:
        """
        Enable access point mode
        
        Only enables AP mode if:
        - WiFi is NOT connected AND
        - Ethernet is NOT connected
        
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
            
            if not self.has_hostapd or not self.has_dnsmasq:
                return False, "hostapd and dnsmasq are required for AP mode"
            
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
                logger.info("AP mode enabled successfully")
                return True, "AP mode enabled"
            except Exception as e:
                logger.error(f"Error starting AP services: {e}")
                return False, str(e)
        except Exception as e:
            logger.error(f"Error enabling AP mode: {e}")
            return False, str(e)
    
    def disable_ap_mode(self) -> Tuple[bool, str]:
        """
        Disable access point mode
        
        Returns:
            Tuple of (success, message)
        """
        try:
            if not self._is_ap_mode_active():
                return True, "AP mode not active"
            
            # Stop services
            try:
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
                
                # Clean up wlan0 IP configuration
                subprocess.run(
                    ["sudo", "ip", "addr", "del", "192.168.4.1/24", "dev", "wlan0"],
                    capture_output=True,
                    timeout=10
                )
                
                # Restart NetworkManager to restore normal WiFi operation
                subprocess.run(
                    ["sudo", "systemctl", "restart", "NetworkManager"],
                    capture_output=True,
                    timeout=15
                )
                
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
            ap_password = self.config.get("ap_password", DEFAULT_AP_PASSWORD)
            ap_channel = self.config.get("ap_channel", DEFAULT_AP_CHANNEL)
            
            config_content = f"""interface=wlan0
driver=nl80211
ssid={ap_ssid}
hw_mode=g
channel={ap_channel}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={ap_password}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
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
        - Ethernet is NOT connected
        
        Always auto-disables AP mode when WiFi or Ethernet connects.
        
        This should be called periodically by a background service.
        
        Returns:
            True if AP mode state changed, False otherwise
        """
        try:
            status = self.get_wifi_status()
            ethernet_connected = self._is_ethernet_connected()
            ap_active = self._is_ap_mode_active()
            auto_enable = self.config.get("auto_enable_ap_mode", True)  # Default: True
            
            # Determine if we should have AP mode active
            # AP mode should only be auto-enabled if:
            # - auto_enable_ap_mode is True AND
            # - WiFi is NOT connected AND
            # - Ethernet is NOT connected
            should_have_ap = auto_enable and not status.connected and not ethernet_connected
            
            if should_have_ap and not ap_active:
                # Should have AP but don't - enable AP mode (only if auto-enable is on)
                success, message = self.enable_ap_mode()
                if success:
                    logger.info("Auto-enabled AP mode (no WiFi or Ethernet connection)")
                    return True
                else:
                    logger.debug(f"Did not enable AP mode: {message}")
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
                        return True
                    else:
                        logger.warning(f"Failed to auto-disable AP mode: {message}")
                elif not auto_enable:
                    # AP is active but auto_enable is disabled - this means it was manually enabled
                    # Don't disable it automatically, let it stay active
                    logger.debug("AP mode is active (manually enabled), keeping active")
            
            return False
        except Exception as e:
            logger.error(f"Error checking AP mode: {e}")
            return False

