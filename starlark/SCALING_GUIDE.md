# Scaling Tronbyte Widgets to Larger Displays

Guide for displaying 64x32 Tronbyte widgets on larger LED matrix displays.

## Overview

Tronbyte widgets are designed for 64x32 pixel displays (Tidbyt's native resolution). When using them on larger displays like 128x64, 192x96, or 128x32, you have several scaling strategies available.

---

## Scaling Strategies

### 1. **Pixlet Magnification** (Best Quality) ⭐

**How it works:** Pixlet renders at higher resolution before converting to WebP.

**Configuration:**
```json
{
  "magnify": 2  // 1=64x32, 2=128x64, 3=192x96, 4=256x128
}
```

**Advantages:**
- ✅ Best visual quality
- ✅ Text remains sharp
- ✅ Animations stay smooth
- ✅ Native rendering at target resolution

**Disadvantages:**
- ⚠️ Slower rendering (2-3x time for magnify=2)
- ⚠️ Higher CPU usage
- ⚠️ Larger cache files

**When to use:**
- Large displays (128x64+)
- Text-heavy apps
- When quality matters more than speed

**Example:**
```
Original: 64x32 pixels
magnify=2: Renders at 128x64
magnify=3: Renders at 192x96
```

---

### 2. **Post-Render Scaling** (Fast)

**How it works:** Render at 64x32, then scale the output image.

**Configuration:**
```json
{
  "magnify": 1,
  "scale_output": true,
  "scale_method": "nearest"  // or "bilinear", "bicubic", "lanczos"
}
```

**Scale Methods:**

| Method | Quality | Speed | Best For |
|--------|---------|-------|----------|
| `nearest` | Pixel-perfect | Fastest | Retro/pixel art look |
| `bilinear` | Smooth | Fast | General use |
| `bicubic` | Smoother | Medium | Photos/gradients |
| `lanczos` | Smoothest | Slowest | Maximum quality |

**Advantages:**
- ✅ Fast rendering
- ✅ Low CPU usage
- ✅ Small cache files
- ✅ Works with any display size

**Disadvantages:**
- ❌ Text may look pixelated
- ❌ Loss of detail
- ❌ Not true high-resolution

**When to use:**
- Lower-powered devices (Raspberry Pi Zero)
- Fast refresh rates needed
- Non-text heavy apps

---

### 3. **Centering** (No Scaling)

**How it works:** Display widget at native 64x32 size, centered on larger display.

**Configuration:**
```json
{
  "center_small_output": true,
  "scale_output": true
}
```

**Visual Example:**
```
128x64 Display:
┌────────────────────────┐
│        (black)         │
│    ┌──────────┐       │
│    │ 64x32 app│       │ ← Widget at native size
│    └──────────┘       │
│        (black)         │
└────────────────────────┘
```

**Advantages:**
- ✅ Perfect quality
- ✅ Fast rendering
- ✅ No distortion

**Disadvantages:**
- ❌ Wastes display space
- ❌ May look small

**When to use:**
- Want pixel-perfect quality
- Display much larger than 64x32
- Willing to sacrifice screen real estate

---

## Configuration Examples

### For 128x64 Display (2x larger)

**Option A: High Quality (Recommended)**
```json
{
  "magnify": 2,
  "scale_output": true,
  "scale_method": "nearest",
  "center_small_output": false
}
```
Result: Native 128x64 rendering, pixel-perfect scaling

**Option B: Performance**
```json
{
  "magnify": 1,
  "scale_output": true,
  "scale_method": "bilinear",
  "center_small_output": false
}
```
Result: Fast 64x32 render, smooth 2x upscale

**Option C: Quality Preservation**
```json
{
  "magnify": 1,
  "scale_output": true,
  "scale_method": "nearest",
  "center_small_output": true
}
```
Result: Native 64x32 centered on black background

---

### For 192x96 Display (3x larger)

**High Quality:**
```json
{
  "magnify": 3,
  "scale_output": true,
  "scale_method": "nearest"
}
```

**Balanced:**
```json
{
  "magnify": 2,
  "scale_output": true,
  "scale_method": "lanczos"
}
```
Result: 128x64 render + 1.5x upscale

---

### For 128x32 Display (2x width only)

**Stretch Horizontal:**
```json
{
  "magnify": 1,
  "scale_output": true,
  "scale_method": "bilinear"
}
```
Result: 64x32 stretched to 128x32 (aspect ratio changed)

**Better: Render at 2x, crop height:**
Use magnify=2 (128x64), then the system will scale to 128x32

---

## Performance Impact

### Rendering Time Comparison

| Display Size | magnify=1 | magnify=2 | magnify=3 | magnify=4 |
|--------------|-----------|-----------|-----------|-----------|
| 64x32 | 2-5s | - | - | - |
| 128x64 | 2-5s + scale | 5-12s | - | - |
| 192x96 | 2-5s + scale | 5-12s + scale | 12-25s | - |
| 256x128 | 2-5s + scale | 5-12s + scale | 12-25s + scale | 25-50s |

**Cache Recommendation:** Use longer cache TTL for higher magnification.

### Memory Usage

| magnify | WebP Size | RAM Usage |
|---------|-----------|-----------|
| 1 | ~50-200KB | ~5MB |
| 2 | ~150-500KB | ~10MB |
| 3 | ~300-1MB | ~20MB |
| 4 | ~500-2MB | ~35MB |

---

## Recommended Settings by Display Size

### 64x32 (Native)
```json
{
  "magnify": 1,
  "scale_output": false
}
```
No scaling needed!

### 128x64 (Common for Raspberry Pi)
```json
{
  "magnify": 2,
  "scale_output": true,
  "scale_method": "nearest",
  "cache_ttl": 600
}
```

### 128x32 (Wide display)
```json
{
  "magnify": 2,
  "scale_output": true,
  "scale_method": "bilinear",
  "cache_ttl": 600
}
```

### 192x96 (Large display)
```json
{
  "magnify": 3,
  "scale_output": true,
  "scale_method": "nearest",
  "cache_ttl": 900
}
```

### 256x128 (Very large)
```json
{
  "magnify": 4,
  "scale_output": true,
  "scale_method": "nearest",
  "cache_ttl": 1200,
  "background_render": true
}
```

---

## How to Configure

### Via Web UI
1. Navigate to Settings → Plugins
2. Find "Starlark Apps Manager"
3. Click Configure
4. Adjust scaling settings:
   - **magnify** - Pixlet rendering scale
   - **scale_method** - Upscaling algorithm
   - **center_small_output** - Enable centering
5. Save configuration

### Via config.json
Edit `config/config.json`:
```json
{
  "starlark-apps": {
    "enabled": true,
    "magnify": 2,
    "scale_output": true,
    "scale_method": "nearest",
    "center_small_output": false,
    "cache_ttl": 600
  }
}
```

---

## Visual Comparison

### 64x32 → 128x64 Scaling

**Method 1: magnify=1 + nearest**
```
█▀▀▀█   →   ██▀▀▀▀██
█   █   →   ██    ██
█▄▄▄█   →   ██▄▄▄▄██
```
Blocky, pixel-art style

**Method 2: magnify=1 + bilinear**
```
█▀▀▀█   →   ██▀▀▀▀██
█   █   →   ██░░░░██
█▄▄▄█   →   ██▄▄▄▄██
```
Smoother, slight blur

**Method 3: magnify=2 + nearest**
```
█▀▀▀█   →   ██▀▀▀▀██
█   █   →   ██    ██
█▄▄▄█   →   ██▄▄▄▄██
```
Sharp, native resolution

---

## Troubleshooting

### Text Looks Blurry
**Problem:** Using post-render scaling with smooth methods
**Solution:** Use `magnify=2` or `scale_method="nearest"`

### Rendering Too Slow
**Problem:** High magnification on slow device
**Solution:**
- Reduce `magnify` to 1 or 2
- Increase `cache_ttl` to cache longer
- Use `scale_method="nearest"` (fastest)
- Enable `background_render`

### App Looks Too Small
**Problem:** Using `center_small_output=true` on large display
**Solution:**
- Disable centering: `center_small_output=false`
- Increase magnification: `magnify=2` or higher
- Use post-render scaling

### Aspect Ratio Wrong
**Problem:** Display dimensions don't match 2:1 ratio
**Solutions:**
1. Use magnification that matches your aspect ratio
2. Accept distortion with `scale_method="bilinear"`
3. Use centering with black bars

### Out of Memory
**Problem:** Too many apps with high magnification
**Solution:**
- Reduce `magnify` value
- Reduce number of enabled apps
- Increase RAM (upgrade hardware)
- Lower `cache_ttl` to cache less

---

## Advanced: Per-App Scaling

Want different scaling for different apps? Currently global, but you can implement per-app by:

1. Create app-specific config field
2. Override magnify in `_render_app()` based on app
3. Store per-app scaling preferences

**Example use case:** Clock at magnify=3, weather at magnify=2

---

## Best Practices

1. **Start with magnify=1** - Test if post-render scaling is good enough
2. **Increase gradually** - Try magnify=2, then 3 if needed
3. **Monitor performance** - Check CPU usage and render times
4. **Cache aggressively** - Use longer cache_ttl for high magnification
5. **Match your hardware** - Raspberry Pi 4 can handle magnify=3, Zero should use magnify=1
6. **Test with actual apps** - Some apps scale better than others

---

## Technical Details

### How Pixlet Magnification Works

Pixlet's `-m` flag renders the Starlark code at N× resolution:
```bash
pixlet render app.star -m 2 -o output.webp
```

This runs the entire rendering pipeline at higher resolution:
- Text rendering at 2× font size
- Image scaling at 2× dimensions
- Layout calculations at 2× coordinates

Result: True high-resolution output, not just upscaling.

### Scale Method Details

**Nearest Neighbor:**
- Each pixel becomes NxN block
- No interpolation
- Preserves hard edges
- Best for pixel art

**Bilinear:**
- Linear interpolation between pixels
- Smooth but slightly blurry
- Fast computation
- Good for photos

**Bicubic:**
- Cubic interpolation
- Smoother than bilinear
- Slower computation
- Good balance

**Lanczos:**
- Sinc-based resampling
- Sharpest high-quality result
- Slowest computation
- Best for maximum quality

---

## Summary

**For best results on larger displays:**
- Use `magnify` equal to your scale factor (2× = magnify 2)
- Use `scale_method="nearest"` for pixel-perfect
- Increase `cache_ttl` to compensate for slower rendering
- Monitor performance and adjust as needed

**Quick decision tree:**
```
Is your display 2x or larger than 64x32?
├─ Yes → Use magnify=2 or higher
│  └─ Fast device? → magnify=3 for best quality
│  └─ Slow device? → magnify=2 with long cache
└─ No → Use magnify=1 with scale_method
```

Enjoy sharp, beautiful widgets on your large LED matrix! 🎨
