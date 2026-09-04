#!/usr/bin/env bash
#
# Rebuild the rgbmatrix Python binding so it releases the GIL.
#
# WHY THIS EXISTS
# ---------------
# The upstream binding declares FrameCanvas::SwapOnVSync WITHOUT `nogil`
# (cppinc.pxd), unlike SetPixel/Clear/Fill on the lines just above it.
# SwapOnVSync blocks until the panel's next vertical sync -- up to a full
# refresh period on every frame -- so the render thread was holding the GIL
# for most of every frame. Background threads (API fetches, JSON parsing,
# image decode) were starved into long uninterruptible bursts, which in turn
# made the render loop miss refreshes.
#
# Measured on a Pi 4 driving a 2x128x64 chain at limit_refresh_rate_hz=100:
#
#     before   ~44 fps average, 14-17% of frames 41-53ms
#     after    100 fps, median 10.00ms, p95 10.05ms, 0% stalls
#
# The per-pixel blit (SetPixelsPillow) can also release the GIL and walk the
# Pillow buffer row-major, but that is OFF by default and you almost certainly
# want to leave it that way. Row-major changes what a partially-written frame
# looks like: column-major tearing shows as a vertical seam, row-major tearing
# shows as a horizontal split between the panel's upper and lower halves. On a
# 1/32 scan panel that reads as a one-pixel "fold" across the middle of every
# panel -- reported on hardware, and it went away when the blit was reverted.
# Enable with RGB_PATCH_BLIT=1 only if you have measured that you need it;
# essentially all of the gain above comes from the SwapOnVSync change alone.
#
# SAFETY
# ------
# Builds into a scratch directory; touches the installed module only in the
# --install step, and backs up the original first. Roll back at any time with:
#
#     sudo bash scripts/build_rgbmatrix_nogil.sh --rollback
#
# USAGE
#     bash scripts/build_rgbmatrix_nogil.sh              # build only
#     sudo bash scripts/build_rgbmatrix_nogil.sh --install
#     sudo bash scripts/build_rgbmatrix_nogil.sh --rollback
#
set -uo pipefail

# Resolve the invoking user's home, not root's. --install runs under sudo,
# where $HOME is /root, so every default path below pointed somewhere the
# build had never written and the install died with "no built module found".
if [ -n "${SUDO_USER:-}" ]; then
    OWNER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
fi
OWNER_HOME="${OWNER_HOME:-$HOME}"

SRC_TREE="${RGB_SRC_TREE:-$OWNER_HOME/LEDMatrix/rpi-rgb-led-matrix-master}"
BUILD_DIR="${RGB_BUILD_DIR:-$OWNER_HOME/rgbmatrix-nogil-build}"
VENV="${RGB_CYTHON_VENV:-$OWNER_HOME/.cache/ledmatrix-cython}"
BACKUP="${RGB_BACKUP:-$OWNER_HOME/rgbmatrix-core.so.ORIGINAL}"
PATCH_BLIT="${RGB_PATCH_BLIT:-0}"

die() { echo "FATAL: $*" >&2; exit 1; }

py_site() {
    python3 -c 'import rgbmatrix, os; print(os.path.dirname(rgbmatrix.__file__))' 2>/dev/null
}

abi_so() {
    ls "$BUILD_DIR"/bindings/python/rgbmatrix/core.cpython-*.so 2>/dev/null | head -1
}

do_rollback() {
    local dst; dst="$(py_site)"
    [ -n "$dst" ] || die "could not locate the installed rgbmatrix package"
    [ -f "$BACKUP" ] || die "no backup at $BACKUP"
    systemctl stop ledmatrix 2>/dev/null
    cp -a "$BACKUP" "$dst/core.so" || die "restore failed"
    find "$dst" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
    systemctl start ledmatrix 2>/dev/null
    echo "rolled back to the original core.so"
    exit 0
}

do_install() {
    local so dst
    so="$(abi_so)"; [ -n "$so" ] || die "no built module found - run the build first"
    dst="$(py_site)"; [ -n "$dst" ] || die "could not locate the installed rgbmatrix package"

    if [ ! -f "$BACKUP" ]; then
        cp -a "$dst/core.so" "$BACKUP" || die "could not back up the original"
        echo "backed up original core.so -> $BACKUP"
    else
        echo "backup already present at $BACKUP (keeping the true original)"
    fi

    systemctl stop ledmatrix 2>/dev/null
    cp "$so" "$dst/core.so" || die "install failed"
    find "$dst" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
    systemctl start ledmatrix 2>/dev/null

    echo "waiting 25s for the display to come back..."
    sleep 25
    local healthy=1
    systemctl is-active --quiet ledmatrix || healthy=0
    if journalctl -u ledmatrix --since "40 sec ago" --no-pager \
         | grep -qiE "Traceback|ImportError|Segmentation fault|undefined symbol"; then
        healthy=0
    fi
    if [ "$healthy" = "1" ]; then
        echo "SUCCESS - running on the rebuilt binding"
    else
        echo "UNHEALTHY - rolling back"
        cp -a "$BACKUP" "$dst/core.so"
        systemctl restart ledmatrix
        journalctl -u ledmatrix --since "90 sec ago" --no-pager | tail -25
        exit 1
    fi
    exit 0
}

case "${1:-}" in
    --rollback) do_rollback ;;
    --install)  do_install ;;
    "" )        ;;
    *)          die "unknown option: $1" ;;
esac

# ---------------------------------------------------------------- build ----
[ -d "$SRC_TREE" ] || die "matrix source tree not found at $SRC_TREE (set RGB_SRC_TREE)"
command -v g++ >/dev/null || die "g++ not installed (apt install build-essential)"

echo "==> staging a scratch copy at $BUILD_DIR"
rm -rf "$BUILD_DIR"
cp -r "$SRC_TREE" "$BUILD_DIR" || die "copy failed"

echo "==> patching the bindings to release the GIL"
python3 - "$BUILD_DIR" "$PATCH_BLIT" <<'PYEOF' || die "patch failed"
import io
import sys

base = sys.argv[1] + "/bindings/python/rgbmatrix/"
patch_blit = len(sys.argv) > 2 and sys.argv[2] == "1"

# --- declare SwapOnVSync as nogil ---------------------------------------
p = base + "cppinc.pxd"
s = io.open(p, encoding="utf-8").read()
OLD_DECL = "        FrameCanvas *SwapOnVSync(FrameCanvas*, uint8_t)\n"
NEW_DECL = "        FrameCanvas *SwapOnVSync(FrameCanvas*, uint8_t) nogil\n"
if OLD_DECL in s:
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(OLD_DECL, NEW_DECL, 1))
    print("   cppinc.pxd: SwapOnVSync declared nogil")
elif NEW_DECL in s:
    print("   cppinc.pxd: already nogil")
else:
    sys.exit("could not find the SwapOnVSync declaration")

# --- release the GIL across the vsync wait ------------------------------
p = base + "core.pyx"
s = io.open(p, encoding="utf-8").read()

OLD_SWAP = (
    "    def SwapOnVSync(self, FrameCanvas newFrame, uint8_t framerate_fraction = 1):\n"
    "        return __createFrameCanvas("
    "self.__matrix.SwapOnVSync(newFrame.__canvas, framerate_fraction))\n"
)
NEW_SWAP = (
    "    def SwapOnVSync(self, FrameCanvas newFrame, uint8_t framerate_fraction = 1):\n"
    "        # Blocks until the panel's next vertical sync. Holding the GIL\n"
    "        # across that wait starves every other Python thread for most of\n"
    "        # each frame. Pointers are hoisted into C locals so the blocking\n"
    "        # call itself needs no Python state.\n"
    "        cdef cppinc.RGBMatrix* matrix = self.__matrix\n"
    "        cdef cppinc.FrameCanvas* frame = newFrame.__canvas\n"
    "        cdef uint8_t fraction = framerate_fraction\n"
    "        cdef cppinc.FrameCanvas* swapped\n"
    "        with nogil:\n"
    "            swapped = matrix.SwapOnVSync(frame, fraction)\n"
    "        return __createFrameCanvas(swapped)\n"
)
if OLD_SWAP in s:
    s = s.replace(OLD_SWAP, NEW_SWAP, 1)
    print("   core.pyx: SwapOnVSync releases the GIL")
elif "swapped = matrix.SwapOnVSync(frame, fraction)" in s:
    print("   core.pyx: SwapOnVSync already patched")
else:
    sys.exit("could not find the SwapOnVSync body")

# --- optional: release the GIL across the blit --------------------------
OLD_BLIT = (
    "        buffer = get_pillow_buffer(image_capsule)\n"
    "\n"
    "        for col in range(max(0, -xstart), min(width, frame_width - xstart)):\n"
    "            for row in range(max(0, -ystart), min(height, frame_height - ystart)):\n"
    "                pixel = buffer[row][col]\n"
    "                r = (pixel ) & 0xFF\n"
    "                g = (pixel >> 8) & 0xFF\n"
    "                b = (pixel >> 16) & 0xFF\n"
    "                my_canvas.SetPixel(xstart+col, ystart+row, r, g, b)\n"
)
NEW_BLIT = (
    "        buffer = get_pillow_buffer(image_capsule)\n"
    "\n"
    "        # Bounds hoisted so the blit needs no Python state and can run\n"
    "        # without the GIL: it touches only a C buffer and a C++ canvas.\n"
    "        # NOTE: row-major order makes a torn frame show as a horizontal\n"
    "        # split across the panel's halves. See the header before enabling.\n"
    "        cdef int col_start = max(0, -xstart)\n"
    "        cdef int col_end = min(width, frame_width - xstart)\n"
    "        cdef int row_start = max(0, -ystart)\n"
    "        cdef int row_end = min(height, frame_height - ystart)\n"
    "\n"
    "        with nogil:\n"
    "            for row in range(row_start, row_end):\n"
    "                for col in range(col_start, col_end):\n"
    "                    pixel = buffer[row][col]\n"
    "                    r = (pixel ) & 0xFF\n"
    "                    g = (pixel >> 8) & 0xFF\n"
    "                    b = (pixel >> 16) & 0xFF\n"
    "                    my_canvas.SetPixel(xstart+col, ystart+row, r, g, b)\n"
)
if patch_blit:
    if OLD_BLIT in s:
        s = s.replace(OLD_BLIT, NEW_BLIT, 1)
        print("   core.pyx: pixel blit releases the GIL, row-major")
    elif "for row in range(row_start, row_end):" in s:
        print("   core.pyx: blit already patched")
    else:
        sys.exit("could not find the SetPixelsPillow loop")
else:
    print("   core.pyx: blit left unpatched (RGB_PATCH_BLIT=1 to enable)")

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
PYEOF

echo "==> building librgbmatrix.a (this takes a few minutes)"
nice -n 10 make -C "$BUILD_DIR/lib" -j2 >/dev/null 2>&1 \
    || die "library build failed - rerun 'make -C $BUILD_DIR/lib' to see why"
[ -f "$BUILD_DIR/lib/librgbmatrix.a" ] || die "librgbmatrix.a was not produced"

echo "==> preparing Cython"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV" || die "venv failed"
"$VENV/bin/pip" install --quiet cython || die "cython install failed"

cat > "$BUILD_DIR/bindings/python/setup.py" <<'EOF'
from setuptools import setup, Extension
from Cython.Build import cythonize

core = Extension(
    "rgbmatrix.core",
    sources=["rgbmatrix/core.pyx", "rgbmatrix/shims/pillow.c"],
    include_dirs=["../../include", "rgbmatrix/shims"],
    extra_objects=["../../lib/librgbmatrix.a"],
    language="c++",
    extra_compile_args=["-O3", "-Wall", "-fno-exceptions", "-std=c++11"],
    extra_link_args=["-lrt", "-lm", "-lpthread"],
)

setup(name="rgbmatrix",
      ext_modules=cythonize([core], language_level="3str",
                            compiler_directives={"binding": False}))
EOF

echo "==> compiling the extension"
( cd "$BUILD_DIR/bindings/python" && "$VENV/bin/python" setup.py build_ext --inplace ) \
    >/dev/null 2>&1 || die "extension build failed"

SO="$(abi_so)"; [ -n "$SO" ] || die "no .so produced"

# Verify the GIL really is released before anyone installs this.
EXPECTED=1; [ "$PATCH_BLIT" = "1" ] && EXPECTED=2
PAIRS=$(grep -c "PyEval_SaveThread\|Py_UNBLOCK_THREADS" \
        "$BUILD_DIR/bindings/python/rgbmatrix/core.cpp")
[ "$PAIRS" -ge "$EXPECTED" ] \
    || die "generated C++ has $PAIRS GIL-release sites, expected >= $EXPECTED"

echo
echo "BUILT: $SO"
echo "       ($PAIRS GIL-release site(s) in the generated C++)"
echo
echo "Install with:   sudo bash $0 --install"
echo "Roll back with: sudo bash $0 --rollback"
