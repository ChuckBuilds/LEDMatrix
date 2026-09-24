#!/usr/bin/env python3
"""
LED Matrix Web Interface V3 Startup Script
Modern web interface with real-time display preview and plugin management.
"""

import os
import socket
import subprocess
import sys
import logging
from pathlib import Path

logger = logging.getLogger('web_interface.start')

# No route to host, broken pipe, connection reset: a client went away.
_CLIENT_DISCONNECT_ERRNOS = (113, 32, 104)


def get_local_ips():
    """Get list of local IP addresses the service will be accessible on."""
    ips = []

    # Check if AP mode is active
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "hostapd"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip() == "active":
            ips.append("192.168.4.1 (AP Mode)")
    except Exception:  # nosec B110 - AP mode IP detection is non-critical startup info; systemctl may not exist
        pass

    # Get IPs from hostname -I
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            for ip in result.stdout.strip().split():
                ip = ip.strip()
                if ip and not ip.startswith("127.") and ip != "192.168.4.1":
                    ips.append(ip)
    except Exception:  # nosec B110 - hostname -I output parsing; non-critical startup info
        pass

    # Fallback: try socket method
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
                if ip and not ip.startswith("127."):
                    ips.append(ip)
            finally:
                s.close()
        except Exception:
            pass

    return ips if ips else ["localhost"]

def main():
    """Main startup function."""
    # Change to project root directory
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # Add to Python path
    sys.path.insert(0, str(project_root))

    # Configure logging to suppress non-critical socket errors
    # These occur when clients disconnect and are harmless
    werkzeug_logger = logging.getLogger('werkzeug')
    original_log_exception = werkzeug_logger.error

    def log_exception_filtered(message, *args, **kwargs):
        """Filter out non-critical socket errors from werkzeug logs."""
        if isinstance(message, str):
            # Suppress "No route to host" and similar connection errors
            if 'No route to host' in message or 'errno 113' in message:
                # Log at debug level instead of error
                werkzeug_logger.debug(message, *args, **kwargs)
                return
            # Suppress broken pipe errors (client disconnected)
            if 'Broken pipe' in message or 'errno 32' in message:
                werkzeug_logger.debug(message, *args, **kwargs)
                return
        # For exceptions, check if it's a socket error
        if 'exc_info' in kwargs and kwargs['exc_info']:
            exc_type, exc_value, exc_tb = kwargs['exc_info']
            if isinstance(exc_value, OSError):
                if exc_value.errno in _CLIENT_DISCONNECT_ERRNOS:
                    werkzeug_logger.debug(message, *args, **kwargs)
                    return
        # Log everything else normally
        original_log_exception(message, *args, **kwargs)

    werkzeug_logger.error = log_exception_filtered

    # Importing the app also sets up logging, so the lines below reach the
    # journal through it.
    from web_interface.app import app, start_auto_update_scheduler
    start_auto_update_scheduler()

    logger.info("Starting LED Matrix Web Interface V3, binding to 0.0.0.0:5000")
    # get_local_ips() always returns at least "localhost".
    logger.info("Access the interface at:")
    for ip in get_local_ips():
        if "AP Mode" in ip:
            logger.info("  - http://192.168.4.1:5000 (AP Mode - connect to LEDMatrix-Setup WiFi)")
        else:
            logger.info("  - http://%s:5000", ip)

    try:
        # threaded=True is Flask's default since 1.0, but set it explicitly
        # so it's self-documenting: the three /api/v3/stream/* SSE endpoints
        # hold long-lived connections and would starve other requests under
        # a single-threaded server.
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except OSError as e:
        if e.errno not in _CLIENT_DISCONNECT_ERRNOS:
            raise
        werkzeug_logger.debug("Client disconnected: %s", e, exc_info=True)

if __name__ == '__main__':
    main()

