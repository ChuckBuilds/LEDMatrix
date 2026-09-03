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
#     after    100 fps locked,  no stalls observed
#
# This script also releases the GIL across the per-pixel blit
# (SetPixelsPillow) and walks the Pillow buffer row-major instead of
# column-major so each row is contiguous.
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

SRC_TREE="${RGB_SRC_TREE:-$HOME/LEDMatrix/rpi-rgb-led-matrix-master}"
BUILD_DIR="${RGB_BUILD_DIR:-$HOME/rgbmatrix-nogil-build}"
VENV="${RGB_CYTHON_VENV:-$HOME/.cache/ledmatrix-cython}"
BACKUP="${RGB_BACKUP:-$HOME/rgbmatrix-core.so.ORIGINAL}"

die() { echo "FATAL: $*" >&2; exit 1; }

py_site() {
    python3 -c 'import rgbmatrix, os; print(os.path.dirname(rgbmatrix.__file__))' 2>/dev/null
}

abi_so() {
    ls "$BUILD_DIR"/bindings/python/rgbmatrix/core.cpython-*.so 2>/dev/null | head -1
}

do_rollback() {
    local dst; dst="$(py_site)" || die "rgbmatrix not importable"
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
python3 - "$BUILD_DIR" <<'PYEOF' || die "patch failed"
import io, sys
base = sys.argv[1] + "/bindings/python/rgbmatrix/"

p = base + "cppinc.pxd"
s = io.open(p, encoding="utf-8").read()
old = "        FrameCanvas *SwapOnVSync(FrameCanvas*, uint8_t)\n"
new = "        FrameCanvas *SwapOnVSync(FrameCanvas*, uint8_t) nogil\n"
if old in s:
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))
    print("   cppinc.pxd: SwapOnVSync declared nogil")
elif new in s:
    print("   cppinc.pxd: already nogil")
else:
    sys.exit("could not find the SwapOnVSync declaration")

p = base + "core.pyx"
s = io.open(p, encoding="utf-8").read()

old = """    def SwapOnVSync(self, FrameCanvas newFrame, uint8_t framerate_fraction = 1):
        return __createFrameCanvas(self.__matrix.SwapOnVSync(newFrame.__canvas, framerate_fraction))
"""
new = """    def SwapOnVSync(self, FrameCanvas newFrame, uint8_t framerate_fraction = 1):
        # Blocks until the panel's next vertical sync. Holding the GIL across
        # that wait starves every other Python thread for most of each frame.
        cdef cppinc.RGBMatrix* matrix = self.__matrix
        cdef cppinc.FrameCanvas* frame = newFrame.__canvas
        cdef uint8_t fraction = framerate_fraction
        cdef cppinc.FrameCanvas* swapped
        with nogil:
            swapped = matrix.SwapOnVSync(frame, fraction)
        return __createFrameCanvas(swapped)
"""
if old in s:
    s = s.replace(old, new, 1)
    print("   core.pyx: SwapOnVSync releases the GIL")
elif "with nogil:\n            swapped = matrix.SwapOnVSync" in s:
    print("   core.pyx: SwapOnVSync already patched")
else:
    sys.exit("could not find the SwapOnVSync body")

old = """        buffer = get_pillow_buffer(image_capsule)

        for col in range(max(0, -xstart), min(width, frame_width - xstart)):
            for row in range(max(0, -ystart), min(height, frame_height - ystart)):
                pixel = buffer[row][col]
                r = (pixel ) & 0xFF
                g = (pixel >> 8) & 0xFF
                b = (pixel >> 16) & 0xFF
                my_canvas.SetPixel(xstart+col, ystart+row, r, g, b)
"""
new = """        buffer = get_pillow_buffer(image_capsule)

        # Bounds hoisted so the blit needs no Python state and can run without
        # the GIL: it touches only a C buffer and a C++ canvas. Row-major order
        # walks each row contiguously; col-outer re-strided the whole buffer.
        cdef int col_start = max(0, -xstart)
        cdef int col_end = min(width, frame_width - xstart)
        cdef int row_start = max(0, -ystart)
        cdef int row_end = min(height, frame_height - ystart)

        with nogil:
            for row in range(row_start, row_end):
                for col in range(col_start, col_end):
                    pixel = buffer[row][col]
                    r = (pixel ) & 0xFF
                    g = (pixel >> 8) & 0xFF
                    b = (pixel >> 16) & 0xFF
                    my_canvas.SetPixel(xstart+col, ystart+row, r, g, b)
"""
if old in s:
    s = s.replace(old, new, 1)
    print("   core.pyx: pixel blit releases the GIL, row-major")
elif "with nogil:\n            for row in range(row_start, row_end):" in s:
    print("   core.pyx: blit already patched")
else:
    sys.exit("could not find the SetPixelsPillow loop")

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
PAIRS=$(grep -c "PyEval_SaveThread\|Py_UNBLOCK_THREADS" "$BUILD_DIR/bindings/python/rgbmatrix/core.cpp")
[ "$PAIRS" -ge 2 ] || die "generated C++ has only $PAIRS GIL releases, expected >= 2"

echo
echo "BUILT: $SO"
echo "       ($PAIRS GIL-release sites in the generated C++)"
echo
echo "Install with:  sudo bash $0 --install"
echo "Roll back with: sudo bash $0 --rollback"
