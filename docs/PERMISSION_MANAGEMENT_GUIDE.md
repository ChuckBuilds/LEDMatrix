# Permission Management Guide

## Overview

LEDMatrix runs with a dual-user architecture: the main display service runs as `root` (for hardware access), while the web interface runs as a regular user. This guide explains how to properly manage file and directory permissions to ensure both services can access the files they need.

## Table of Contents

1. [Why Permission Management Matters](#why-permission-management-matters)
2. [Permission Utilities](#permission-utilities)
3. [When to Use Permission Utilities](#when-to-use-permission-utilities)
4. [How to Use Permission Utilities](#how-to-use-permission-utilities)
5. [Common Patterns and Examples](#common-patterns-and-examples)
6. [Permission Standards](#permission-standards)
7. [Troubleshooting](#troubleshooting)

---

## Why Permission Management Matters

### The Problem

Without proper permission management, you may encounter errors like:
- `PermissionError: [Errno 13] Permission denied` when saving config files
- `PermissionError` when downloading team logos
- Files created by the root service not accessible by the web user
- Files created by the web user not accessible by the root service

### The Solution

The LEDMatrix codebase includes centralized permission utilities (`src/common/permission_utils.py`) that ensure files and directories are created with appropriate permissions for both users.

---

## Permission Utilities

### Available Functions

The permission utilities module provides the following functions:

#### Directory Management

- `ensure_directory_permissions(path: Path, mode: int = 0o775) -> None`
  - Creates directory if it doesn't exist
  - Sets permissions to the specified mode
  - Default mode: `0o775` (rwxrwxr-x) - group-writable

#### File Management

- `ensure_file_permissions(path: Path, mode: int = 0o644) -> None`
  - Sets permissions on an existing file
  - Default mode: `0o644` (rw-r--r--) - world-readable

#### Mode Helpers

These functions return the appropriate permission mode for different file types:

- `get_config_file_mode(file_path: Path) -> int`
  - Returns `0o640` for secrets files, `0o644` for regular config files

- `get_assets_file_mode() -> int`
  - Returns `0o664` (rw-rw-r--) for asset files (logos, images)

- `get_assets_dir_mode() -> int`
  - Returns `0o2775` (rwxrwsr-x) for asset directories
  - Setgid bit enforces inherited group ownership for new files/directories

- `get_config_dir_mode() -> int`
  - Returns `0o2775` (rwxrwsr-x) for config directories
  - Setgid bit enforces inherited group ownership for new files/directories

- `get_plugin_file_mode() -> int`
  - Returns `0o664` (rw-rw-r--) for plugin files

- `get_plugin_dir_mode() -> int`
  - Returns `0o2775` (rwxrwsr-x) for plugin directories
  - Setgid bit enforces inherited group ownership for new files/directories

- `get_cache_dir_mode() -> int`
  - Returns `0o2775` (rwxrwsr-x) for cache directories
  - Setgid bit enforces inherited group ownership for new files/directories

---

## When to Use Permission Utilities

### Always Use Permission Utilities When:

1. **Creating directories** - Use `ensure_directory_permissions()` instead of `os.makedirs()` or `Path.mkdir()`
2. **Saving files** - Use `ensure_file_permissions()` after writing files
3. **Downloading assets** - Set permissions after downloading logos, images, or other assets
4. **Creating config files** - Set permissions after saving configuration files
5. **Creating cache files** - Set permissions when creating cache directories or files
6. **Plugin file operations** - Set permissions when plugins create their own files/directories

### You Don't Need Permission Utilities When:

1. **Reading files** - Reading doesn't require permission changes
2. **Using core utilities** - Core utilities (LogoHelper, CacheManager, ConfigManager) already handle permissions
3. **Temporary files** - Files in `/tmp` or created with `tempfile` don't need special permissions

---

## How to Use Permission Utilities

### Basic Import

```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode,
    get_config_dir_mode,
    get_config_file_mode
)
```

### Creating a Directory

**Before (incorrect):**
```python
import os
os.makedirs("assets/sports/logos", exist_ok=True)
# Problem: Permissions may not be set correctly
```

**After (correct):**
```python
from pathlib import Path
from src.common.permission_utils import ensure_directory_permissions, get_assets_dir_mode

logo_dir = Path("assets/sports/logos")
ensure_directory_permissions(logo_dir, get_assets_dir_mode())
```

### Saving a File

**Before (incorrect):**
```python
with open("config/my_config.json", 'w') as f:
    json.dump(data, f, indent=4)
# Problem: File may not be readable by root service
```

**After (correct):**
```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_dir_mode,
    get_config_file_mode
)

config_path = Path("config/my_config.json")
# Ensure directory exists with proper permissions
ensure_directory_permissions(config_path.parent, get_config_dir_mode())

# Write file
with open(config_path, 'w') as f:
    json.dump(data, f, indent=4)

# Set file permissions
ensure_file_permissions(config_path, get_config_file_mode(config_path))
```

### Downloading and Saving an Image

**Before (incorrect):**
```python
response = requests.get(image_url)
with open("assets/sports/logo.png", 'wb') as f:
    f.write(response.content)
# Problem: File may not be writable by root service
```

**After (correct):**
```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode
)

logo_path = Path("assets/sports/logo.png")
# Ensure directory exists
ensure_directory_permissions(logo_path.parent, get_assets_dir_mode())

# Download and save
response = requests.get(image_url)
with open(logo_path, 'wb') as f:
    f.write(response.content)

# Set file permissions
ensure_file_permissions(logo_path, get_assets_file_mode())
```

---

## Common Patterns and Examples

### Pattern 1: Config File Save

```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_dir_mode,
    get_config_file_mode
)

def save_config(config_data: dict, config_path: str) -> None:
    """Save configuration file with proper permissions."""
    path = Path(config_path)
    
    # Ensure directory exists
    ensure_directory_permissions(path.parent, get_config_dir_mode())
    
    # Write file
    with open(path, 'w') as f:
        json.dump(config_data, f, indent=4)
    
    # Set permissions
    ensure_file_permissions(path, get_config_file_mode(path))
```

### Pattern 2: Asset Directory Setup

```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    get_assets_dir_mode
)

def setup_asset_directory(base_dir: str, subdir: str) -> Path:
    """Create asset directory with proper permissions."""
    asset_dir = Path(base_dir) / subdir
    ensure_directory_permissions(asset_dir, get_assets_dir_mode())
    return asset_dir
```

### Pattern 3: Plugin File Creation

```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_plugin_dir_mode,
    get_plugin_file_mode
)

def save_plugin_data(plugin_id: str, data: dict) -> None:
    """Save plugin data file with proper permissions."""
    plugin_dir = Path("plugins") / plugin_id
    data_file = plugin_dir / "data.json"
    
    # Ensure plugin directory exists
    ensure_directory_permissions(plugin_dir, get_plugin_dir_mode())
    
    # Write file
    with open(data_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    # Set permissions
    ensure_file_permissions(data_file, get_plugin_file_mode())
```

### Pattern 4: Cache Directory Creation

```python
from pathlib import Path
from src.common.permission_utils import (
    ensure_directory_permissions,
    get_cache_dir_mode
)

def get_cache_directory() -> Path:
    """Get or create cache directory with proper permissions."""
    cache_dir = Path("/var/cache/ledmatrix")
    ensure_directory_permissions(cache_dir, get_cache_dir_mode())
    return cache_dir
```

### Pattern 5: Atomic File Write with Permissions

```python
from pathlib import Path
import tempfile
import os
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_dir_mode,
    get_config_file_mode
)

def save_config_atomic(config_data: dict, config_path: str) -> None:
    """Save config file atomically with proper permissions."""
    path = Path(config_path)
    
    # Ensure directory exists
    ensure_directory_permissions(path.parent, get_config_dir_mode())
    
    # Write to temp file first
    temp_path = path.with_suffix('.tmp')
    with open(temp_path, 'w') as f:
        json.dump(config_data, f, indent=4)
    
    # Set permissions on temp file
    ensure_file_permissions(temp_path, get_config_file_mode(path))
    
    # Atomic move
    temp_path.replace(path)
    
    # Permissions are preserved after move, but ensure they're correct
    ensure_file_permissions(path, get_config_file_mode(path))
```

---

## Permission Standards

### File Permissions

| File Type | Mode | Octal | Description |
|-----------|------|-------|-------------|
| Config files | `rw-r--r--` | `0o644` | Readable by all, writable by owner |
| Secrets files | `rw-r-----` | `0o640` | Readable by owner and group only |
| Asset files | `rw-rw-r--` | `0o664` | Group-writable for root:user access |
| Plugin files | `rw-rw-r--` | `0o664` | Group-writable for root:user access |

### Directory Permissions

| Directory Type | Mode | Octal | Description |
|----------------|------|-------|-------------|
| Config directories | `rwxr-xr-x` | `0o755` | Traversable by all, writable by owner |
| Asset directories | `rwxrwxr-x` | `0o775` | Group-writable for root:user access |
| Plugin directories | `rwxrwxr-x` | `0o775` | Group-writable for root:user access |
| Cache directories | `rwxrwxr-x` | `0o775` | Group-writable for root:user access |

### Why These Permissions?

- **Group-writable (775/664)**: Allows both root service and web user to read/write files
- **World-readable (644)**: Config files need to be readable by root service
- **Restricted (640)**: Secrets files should only be readable by owner and group

---

## Troubleshooting

### Common Issues

#### Issue: Permission denied when saving config

**Symptoms:**
```
PermissionError: [Errno 13] Permission denied: 'config/config.json'
```

**Solution:**
Ensure you're using `ensure_directory_permissions()` and `ensure_file_permissions()`:

```python
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_config_dir_mode,
    get_config_file_mode
)

path = Path("config/config.json")
ensure_directory_permissions(path.parent, get_config_dir_mode())
# ... write file ...
ensure_file_permissions(path, get_config_file_mode(path))
```

#### Issue: Logo downloads fail with permission errors

**Symptoms:**
```
PermissionError: Cannot write to directory assets/sports/logos
```

**Solution:**
Use permission utilities when creating directories and saving files:

```python
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_assets_dir_mode,
    get_assets_file_mode
)

logo_path = Path("assets/sports/logos/team.png")
ensure_directory_permissions(logo_path.parent, get_assets_dir_mode())
# ... download and save ...
ensure_file_permissions(logo_path, get_assets_file_mode())
```

#### Issue: Files created by root service not accessible by web user

**Symptoms:**
- Web interface can't read files created by the service
- Files show as owned by root with restrictive permissions

**Solution:**
Always use permission utilities when creating files. The utilities set group-writable permissions (664/775) that allow both users to access files.

#### Issue: Plugin can't write to its directory

**Symptoms:**
```
PermissionError: Cannot write to plugins/my-plugin/data.json
```

**Solution:**
Use permission utilities in your plugin:

```python
from src.common.permission_utils import (
    ensure_directory_permissions,
    ensure_file_permissions,
    get_plugin_dir_mode,
    get_plugin_file_mode
)

# In your plugin code
plugin_dir = Path("plugins") / self.plugin_id
ensure_directory_permissions(plugin_dir, get_plugin_dir_mode())
# ... create files ...
ensure_file_permissions(file_path, get_plugin_file_mode())
```

### Verification

To verify permissions are set correctly:

```bash
# Check file permissions
ls -l config/config.json
# Should show: -rw-r--r-- or -rw-rw-r--

# Check directory permissions
ls -ld assets/sports/logos
# Should show: drwxrwxr-x or drwxr-xr-x

# Check if both users can access
sudo -u root test -r config/config.json && echo "Root can read"
sudo -u $USER test -r config/config.json && echo "User can read"
```

### Manual Fix

If you need to manually fix permissions:

```bash
# Fix assets directory
sudo ./scripts/fix_perms/fix_assets_permissions.sh

# Fix plugin directory
sudo ./scripts/fix_perms/fix_plugin_permissions.sh

# Fix config directory
sudo chmod 755 config
sudo chmod 644 config/config.json
sudo chmod 640 config/config_secrets.json
```

---

## Best Practices

1. **Always use permission utilities** when creating files or directories
2. **Use the appropriate mode helper** (`get_assets_file_mode()`, etc.) rather than hardcoding modes
3. **Set directory permissions before creating files** in that directory
4. **Set file permissions immediately after writing** the file
5. **Use atomic writes** (temp file + move) for critical files like config
6. **Test with both users** - verify files work when created by root service and web user

---

## Integration with Core Utilities

Many core utilities already handle permissions automatically:

- **LogoHelper** (`src/common/logo_helper.py`) - Sets permissions when downloading logos
- **LogoDownloader** (`src/logo_downloader.py`) - Sets permissions for directories and files
- **CacheManager** - Sets permissions when creating cache directories
- **ConfigManager** - Sets permissions when saving config files
- **PluginManager** - Sets permissions for plugin directories and marker files

If you're using these utilities, you don't need to manually set permissions. However, if you're creating files directly (not through these utilities), you should use the permission utilities.

---

## Summary

- **Always use** `ensure_directory_permissions()` when creating directories
- **Always use** `ensure_file_permissions()` after writing files
- **Use mode helpers** (`get_assets_file_mode()`, etc.) for consistency
- **Core utilities handle permissions** - you only need to set permissions for custom file operations
- **Group-writable permissions (664/775)** allow both root service and web user to access files

For questions or issues, refer to the troubleshooting section or check existing code in the LEDMatrix codebase for examples.

