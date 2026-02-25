#!/bin/bash
#
# Download Pixlet binaries for bundled distribution
#
# This script downloads Pixlet binaries from the Tronbyte fork
# for multiple architectures to support various platforms.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BIN_DIR="$PROJECT_ROOT/bin/pixlet"

# Pixlet version to download (use 'latest' to auto-detect)
PIXLET_VERSION="${PIXLET_VERSION:-latest}"

# GitHub repository (Tronbyte fork)
REPO="tronbyt/pixlet"

echo "========================================"
echo "Pixlet Binary Download Script"
echo "========================================"

# Auto-detect latest version if needed
if [ "$PIXLET_VERSION" = "latest" ]; then
    echo "Detecting latest version..."
    PIXLET_VERSION=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name"' | sed -E 's/.*"([^"]+)".*/\1/')
    if [ -z "$PIXLET_VERSION" ]; then
        echo "Failed to detect latest version, using fallback"
        PIXLET_VERSION="v0.50.2"
    fi
fi

echo "Version: $PIXLET_VERSION"
echo "Target directory: $BIN_DIR"
echo ""

# Create bin directory if it doesn't exist
mkdir -p "$BIN_DIR"

# New naming convention: pixlet_v0.50.2_linux-arm64.tar.gz
# Only download ARM64 Linux binary for Raspberry Pi
declare -A ARCHITECTURES=(
    ["linux-arm64"]="pixlet_${PIXLET_VERSION}_linux-arm64.tar.gz"
)

download_binary() {
    local arch="$1"
    local archive_name="$2"
    local binary_name="pixlet-${arch}"

    local output_path="$BIN_DIR/$binary_name"

    # Skip if already exists
    if [ -f "$output_path" ]; then
        echo "✓ $binary_name already exists, skipping..."
        return 0
    fi

    echo "→ Downloading $arch..."

    # Construct download URL
    local url="https://github.com/${REPO}/releases/download/${PIXLET_VERSION}/${archive_name}"

    # Download to temp directory (use project-local temp to avoid /tmp permission issues)
    local temp_dir
    temp_dir=$(mktemp -d -p "$PROJECT_ROOT" -t pixlet_download.XXXXXXXXXX)
    local temp_file="$temp_dir/$archive_name"

    if ! curl -L -o "$temp_file" "$url" 2>/dev/null; then
        echo "✗ Failed to download $arch"
        rm -rf "$temp_dir"
        return 1
    fi

    # Extract binary
    echo "  Extracting..."
    if ! tar -xzf "$temp_file" -C "$temp_dir"; then
        echo "✗ Failed to extract archive: $temp_file"
        rm -rf "$temp_dir"
        return 1
    fi

    # Find the pixlet binary in extracted files
    local extracted_binary
    extracted_binary=$(find "$temp_dir" -name "pixlet" | head -n 1)

    if [ -z "$extracted_binary" ]; then
        echo "✗ Binary not found in archive"
        rm -rf "$temp_dir"
        return 1
    fi

    # Move to final location
    mv "$extracted_binary" "$output_path"

    # Make executable
    chmod +x "$output_path"

    # Clean up
    rm -rf "$temp_dir"

    # Verify
    local size
    size=$(stat -f%z "$output_path" 2>/dev/null || stat -c%s "$output_path" 2>/dev/null || echo "unknown")
    if [ "$size" = "unknown" ]; then
        echo "✓ Downloaded $binary_name"
    else
        echo "✓ Downloaded $binary_name ($(numfmt --to=iec-i --suffix=B $size 2>/dev/null || echo "${size} bytes"))"
    fi

    return 0
}

# Download binaries for each architecture
success_count=0
total_count=${#ARCHITECTURES[@]}

for arch in "${!ARCHITECTURES[@]}"; do
    if download_binary "$arch" "${ARCHITECTURES[$arch]}"; then
        ((success_count++))
    fi
done

echo ""
echo "========================================"
echo "Download complete: $success_count/$total_count succeeded"
echo "========================================"

# List downloaded binaries
echo ""
echo "Installed binaries:"
if compgen -G "$BIN_DIR/*" > /dev/null 2>&1; then
    ls -lh "$BIN_DIR"/*
else
    echo "No binaries found"
fi

exit 0
