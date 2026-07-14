/**
 * ComposerCanvas — stateless LED matrix canvas renderer.
 *
 * Coordinate system: LED pixels (integers). All drawing multiplies by SCALE.
 * PIL draw.text(x,y) is top-left; canvas fillText(x,y) is baseline.
 *   → Canvas text cy = (actualY + fontSizePx) * SCALE
 *
 * Anchors: element x/y are offsets from their anchor point:
 *   xAnchor=null/'left'  → x is fixed offset from left
 *   xAnchor='center'     → x is offset from width/2
 *   xAnchor='right'      → x is offset inward from right edge
 *   yAnchor follows the same pattern with 'top'/'middle'/'bottom'
 *
 * Breakpoints: elements with minWidth > currentMatrixW are rendered at 25% opacity.
 *
 * Resize handles: drawn on selected rectangles; 8 handles (corners + edge mids).
 */
window.ComposerCanvas = (() => {
  'use strict';

  let _canvas = null;
  let _ctx = null;
  let _showGrid = true;

  const DISPLAY_PRESETS = [
    { label: '64×32',   w: 64,  h: 32 },
    { label: '128×32',  w: 128, h: 32 },
    { label: '128×64',  w: 128, h: 64 },
    { label: '256×32',  w: 256, h: 32 },
    { label: '256×64',  w: 256, h: 64 },
  ];

  const FONT_MAP = {
    press_start:   { family: "'PressStart2P', monospace", sizePx: 8,  charW: 8 },
    four_by_six:   { family: 'monospace',                sizePx: 6,  charW: 4 },
    five_by_seven: { family: 'monospace',                sizePx: 7,  charW: 5 },
  };

  const ELEMENT_DEFAULTS = {
    text: {
      text: 'Hello', font: 'press_start',
      r: 255, g: 255, b: 255,
      text2: '', lineSpacing: 2, textAlign: 'left',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    dynamic_text: {
      binding: { source: 'config', key: '', format: null },
      font: 'press_start', textAlign: 'left',
      r: 255, g: 200, b: 100,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    clock: {
      format: '%H:%M', font: 'press_start',
      r: 100, g: 255, b: 100,
      format2: '', lineSpacing: 2, textAlign: 'left',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    rectangle: {
      width: 20, height: 8,
      fillR: 0, fillG: 0, fillB: 128, hasFill: true,
      outR: 255, outG: 255, outB: 255, hasOutline: true,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    ellipse: {
      width: 24, height: 12,
      fillR: 0, fillG: 100, fillB: 200, hasFill: true,
      outR: 100, outG: 180, outB: 255, hasOutline: true,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    arc: {
      width: 24, height: 24,
      startAngle: 0, endAngle: 270, lineWidth: 2,
      r: 255, g: 200, b: 0,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    pixel: {
      r: 255, g: 255, b: 255,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    rounded_rectangle: {
      width: 24, height: 10, borderRadius: 3,
      fillR: 0, fillG: 80, fillB: 180, hasFill: true,
      outR: 120, outG: 180, outB: 255, hasOutline: true,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    line: {
      x0: 0, y0: 16, x1: 63, y1: 16,
      r: 180, g: 180, b: 180, lineWidth: 1,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    divider: {
      orientation: 'horizontal', y: 16, x: 64,
      r: 100, g: 100, b: 100,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    progress_bar: {
      barWidth: 60, barHeight: 6,
      binding: { source: 'config', key: '', format: null },
      r: 80, g: 200, b: 80,
      bgR: 30, bgG: 30, bgB: 30, hasBg: true,
      outR: 100, outG: 100, outB: 100, hasOutline: true,
      previewPct: 65,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    countdown: {
      binding: { source: 'config', key: '', format: null },
      countdownFormat: 'dh',
      font: 'four_by_six', textAlign: 'left',
      r: 255, g: 180, b: 0,
      previewText: '42d 3h',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    marquee: {
      text: 'Scrolling text', font: 'press_start',
      r: 255, g: 255, b: 255,
      scrollSpeed: 1, gap: 16, direction: 'left',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    section: {
      label: 'Section',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    pips: {
      count: 5, filled: 3, pipSize: 4, pipSpacing: 2,
      r: 255, g: 200, b: 0,
      emptyR: 50, emptyG: 50, emptyB: 50, showEmpty: true,
      binding: { source: 'config', key: '', format: null },
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    sparkline: {
      width: 40, height: 12,
      barCount: 8, barSpacing: 1,
      r: 80, g: 200, b: 120,
      bgR: 30, bgG: 30, bgB: 30, hasBg: false,
      binding: { source: 'config', key: '', format: null },
      previewData: '0.3,0.6,0.4,0.8,0.5,0.9,0.7,0.85',
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
    gauge: {
      width: 32, height: 32,
      startAngle: 135, endAngle: 45, lineWidth: 3,
      binding: { source: 'config', key: '', format: null },
      r: 80, g: 220, b: 80,
      trackR: 40, trackG: 40, trackB: 40, hasTrack: true,
      showLabel: true, font: 'four_by_six', labelR: 200, labelG: 200, labelB: 200,
      previewPct: 65,
      xAnchor: null, yAnchor: null, minWidth: 0, locked: false, blink: false, visible: true,
    },
  };

  // ── Anchor resolution ────────────────────────────────────────────────
  function resolveAnchor(val, anchor, dim) {
    if (!anchor || anchor === 'left' || anchor === 'top') return val;
    if (anchor === 'center' || anchor === 'middle') return Math.floor(dim / 2) + val;
    if (anchor === 'right' || anchor === 'bottom') return dim - val;
    return val;
  }

  function computeActualPos(el, matrixW, matrixH) {
    const ax = resolveAnchor(el.x ?? el.x0 ?? 0, el.xAnchor, matrixW);
    const ay = resolveAnchor(el.y ?? el.y0 ?? 0, el.yAnchor, matrixH);
    return { x: ax, y: ay };
  }

  // ── Bounding box (LED pixel space) ──────────────────────────────────
  function getBoundingBox(el, matrixW, matrixH) {
    const { x: ax, y: ay } = computeActualPos(el, matrixW, matrixH);
    const finfo = FONT_MAP[el.font] || FONT_MAP.press_start;

    switch (el.type) {
      case 'text': {
        const t1 = el.text || '', t2 = el.text2 || '';
        const w = Math.max(t1.length, t2.length) * finfo.charW;
        const h = t2 ? finfo.sizePx * 2 + (el.lineSpacing ?? 2) : finfo.sizePx;
        const bx = el.textAlign === 'center' ? ax - w / 2 : el.textAlign === 'right' ? ax - w : ax;
        return { x: bx, y: ay, w, h };
      }
      case 'dynamic_text': {
        const key = el.binding?.key || '?';
        const w = (`{${key}}`).length * finfo.charW;
        const bx = el.textAlign === 'center' ? ax - w / 2 : el.textAlign === 'right' ? ax - w : ax;
        return { x: bx, y: ay, w, h: finfo.sizePx };
      }
      case 'clock': {
        const t1 = el.format || '%H:%M', t2 = el.format2 || '';
        const w = Math.max(t1.length, t2.length) * finfo.charW;
        const h = t2 ? finfo.sizePx * 2 + (el.lineSpacing ?? 2) : finfo.sizePx;
        const bx = el.textAlign === 'center' ? ax - w / 2 : el.textAlign === 'right' ? ax - w : ax;
        return { x: bx, y: ay, w, h };
      }
      case 'countdown': {
        const pt = el.previewText || '--d --h';
        const w = pt.length * finfo.charW;
        const bx = el.textAlign === 'center' ? ax - w / 2 : el.textAlign === 'right' ? ax - w : ax;
        return { x: bx, y: ay, w, h: finfo.sizePx };
      }
      case 'rectangle':
      case 'rounded_rectangle':
      case 'ellipse':
      case 'arc':
        return { x: ax, y: ay, w: el.width, h: el.height };
      case 'pixel':
        return { x: ax, y: ay, w: 1, h: 1 };
      case 'line':
        return {
          x: Math.min(el.x0, el.x1), y: Math.min(el.y0, el.y1),
          w: Math.max(1, Math.abs(el.x1 - el.x0)),
          h: Math.max(1, Math.abs(el.y1 - el.y0)),
        };
      case 'divider':
        return el.orientation === 'horizontal'
          ? { x: 0,  y: ay, w: matrixW, h: 1 }
          : { x: ax, y: 0,  w: 1,       h: matrixH };
      case 'progress_bar':
        return { x: ax, y: ay, w: el.barWidth ?? 60, h: el.barHeight ?? 6 };
      case 'marquee': {
        const mfinfo = FONT_MAP[el.font] || FONT_MAP.press_start;
        return { x: 0, y: ay, w: matrixW, h: mfinfo.sizePx };
      }
      case 'gauge':
        return { x: ax, y: ay, w: el.width ?? 32, h: el.height ?? 32 };
      case 'sparkline':
        return { x: ax, y: ay, w: el.width ?? 40, h: el.height ?? 12 };
      case 'pips': {
        const pc = el.count ?? 5, ps = el.pipSize ?? 4, pg = el.pipSpacing ?? 2;
        return { x: ax, y: ay, w: pc * ps + (pc - 1) * pg, h: ps };
      }
      case 'section':
        return { x: ax, y: ay, w: 0, h: 0 };
      default:
        return { x: ax, y: ay, w: 4, h: 4 };
    }
  }

  // ── Resize handle support ─────────────────────────────────────────────
  // Returns 8 handle points for a rectangle in LED pixel space
  function _getRectHandles(el, matrixW, matrixH) {
    const { x: ax, y: ay } = computeActualPos(el, matrixW, matrixH);
    const w = el.width, h = el.height;
    const cx = ax + w / 2, cy = ay + h / 2;
    return {
      nw: { x: ax,      y: ay      },
      n:  { x: cx,      y: ay      },
      ne: { x: ax + w,  y: ay      },
      w:  { x: ax,      y: cy      },
      e:  { x: ax + w,  y: cy      },
      sw: { x: ax,      y: ay + h  },
      s:  { x: cx,      y: ay + h  },
      se: { x: ax + w,  y: ay + h  },
    };
  }

  // Returns the handle direction under LED-space point (lx, ly), or null
  function getResizeHandle(el, lx, ly, matrixW, matrixH) {
    if (!['rectangle', 'rounded_rectangle', 'ellipse', 'arc', 'gauge', 'sparkline'].includes(el.type)) return null;
    const handles = _getRectHandles(el, matrixW, matrixH);
    const PAD = 4;
    for (const [dir, pt] of Object.entries(handles)) {
      if (Math.abs(lx - pt.x) <= PAD && Math.abs(ly - pt.y) <= PAD) return dir;
    }
    return null;
  }

  const _HANDLE_CURSORS = {
    nw: 'nw-resize', n: 'n-resize', ne: 'ne-resize',
    w: 'w-resize', e: 'e-resize',
    sw: 'sw-resize', s: 's-resize', se: 'se-resize',
  };
  function getCursorForHandle(handle) {
    return _HANDLE_CURSORS[handle] || 'crosshair';
  }

  // ── Hit test ─────────────────────────────────────────────────────────
  function hitTest(el, lx, ly, matrixW, matrixH) {
    const PAD = 3;
    const bb = getBoundingBox(el, matrixW, matrixH);
    return (
      lx >= bb.x - PAD && lx <= bb.x + bb.w + PAD &&
      ly >= bb.y - PAD && ly <= bb.y + bb.h + PAD
    );
  }

  // ── Draw a single element ─────────────────────────────────────────────
  function _drawElement(ctx, el, SCALE, matrixW, matrixH, opts = {}) {
    const s = SCALE;
    const { x: ax, y: ay } = computeActualPos(el, matrixW, matrixH);
    const belowBreakpoint = el.minWidth > 0 && matrixW < el.minWidth;
    const hidden = el.visible === false;

    ctx.save();
    if (hidden) ctx.globalAlpha = 0.12;
    else if (belowBreakpoint) ctx.globalAlpha = 0.25;

    // Blink animation: when blinkOff, fully hide blinking elements
    if (el.blink) {
      if (opts.blinkOff) { ctx.restore(); return; }
      ctx.globalAlpha *= 0.55;
    }

    // Helper: compute draw X for text alignment
    const _textX = (text, finfo) => {
      const tw = text.length * finfo.charW * s;
      if (el.textAlign === 'center') return ax * s - tw / 2;
      if (el.textAlign === 'right')  return ax * s - tw;
      return ax * s;
    };

    try {
      switch (el.type) {
        case 'text':
        case 'dynamic_text':
        case 'clock': {
          const finfo = FONT_MAP[el.font] || FONT_MAP.press_start;
          const key = el.binding?.key || '?';
          const pv = opts.previewValues?.[key];
          // Substitute {variable} tokens in text using previewValues
          const _subVars = str => (str || '').replace(/\{(\w+)\}/g, (_, k) => {
            const v = opts.previewValues?.[k];
            return v !== undefined && v !== '' ? String(v) : `{${k}}`;
          });
          const displayText =
            el.type === 'text'    ? _subVars(el.text || '')
            : el.type === 'clock' ? (el.format || '%H:%M')
            : (pv !== undefined && pv !== '' ? String(pv) : `{${key}}`);
          ctx.font = `${finfo.sizePx * s}px ${finfo.family}`;
          ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.fillText(displayText, _textX(displayText, finfo), (ay + finfo.sizePx) * s);
          // Second line (text and clock)
          if (el.type === 'text' && el.text2) {
            const t2 = _subVars(el.text2);
            const y2 = ay + finfo.sizePx + (el.lineSpacing ?? 2);
            ctx.fillText(t2, _textX(t2, finfo), (y2 + finfo.sizePx) * s);
          }
          if (el.type === 'clock' && el.format2) {
            const y2 = ay + finfo.sizePx + (el.lineSpacing ?? 2);
            ctx.fillText(el.format2, _textX(el.format2, finfo), (y2 + finfo.sizePx) * s);
          }
          break;
        }

        case 'countdown': {
          const finfo = FONT_MAP[el.font] || FONT_MAP.press_start;
          const t = el.previewText || '--d --h';
          ctx.font = `${finfo.sizePx * s}px ${finfo.family}`;
          ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.fillText(t, _textX(t, finfo), (ay + finfo.sizePx) * s);
          break;
        }

        case 'rectangle': {
          const rx = ax * s, ry = ay * s;
          const rw = el.width * s, rh = el.height * s;
          if (el.hasFill) {
            ctx.fillStyle = `rgb(${el.fillR},${el.fillG},${el.fillB})`;
            ctx.fillRect(rx, ry, rw, rh);
          }
          if (el.hasOutline) {
            ctx.strokeStyle = `rgb(${el.outR},${el.outG},${el.outB})`;
            ctx.lineWidth = 1;
            ctx.strokeRect(rx, ry, rw, rh);
          }
          break;
        }

        case 'ellipse': {
          const cx = (ax + el.width / 2) * s;
          const cy = (ay + el.height / 2) * s;
          const rx = (el.width / 2) * s;
          const ry = (el.height / 2) * s;
          ctx.beginPath();
          ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
          if (el.hasFill) {
            ctx.fillStyle = `rgb(${el.fillR},${el.fillG},${el.fillB})`;
            ctx.fill();
          }
          if (el.hasOutline) {
            ctx.strokeStyle = `rgb(${el.outR},${el.outG},${el.outB})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
          break;
        }

        case 'arc': {
          const cx = (ax + el.width / 2) * s;
          const cy = (ay + el.height / 2) * s;
          const rx = (el.width / 2) * s;
          const ry = (el.height / 2) * s;
          // PIL: 0°=right, clockwise. Canvas: same with anticlockwise=false
          const startRad = (el.startAngle ?? 0) * Math.PI / 180;
          const endRad = (el.endAngle ?? 270) * Math.PI / 180;
          ctx.beginPath();
          ctx.ellipse(cx, cy, rx, ry, 0, startRad, endRad, false);
          ctx.strokeStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.lineWidth = Math.max(1, el.lineWidth || 2);
          ctx.stroke();
          break;
        }

        case 'pixel': {
          ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.fillRect(ax * s, ay * s, s, s);
          break;
        }

        case 'rounded_rectangle': {
          const rx = ax * s, ry = ay * s;
          const rw = el.width * s, rh = el.height * s;
          const rad = Math.min((el.borderRadius ?? 3) * s, rw / 2, rh / 2);
          ctx.beginPath();
          ctx.roundRect(rx, ry, rw, rh, rad);
          if (el.hasFill) {
            ctx.fillStyle = `rgb(${el.fillR},${el.fillG},${el.fillB})`;
            ctx.fill();
          }
          if (el.hasOutline) {
            ctx.strokeStyle = `rgb(${el.outR},${el.outG},${el.outB})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
          break;
        }

        case 'line': {
          ctx.strokeStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.lineWidth = Math.max(1, el.lineWidth || 1);
          ctx.beginPath();
          ctx.moveTo(el.x0 * s, el.y0 * s);
          ctx.lineTo(el.x1 * s, el.y1 * s);
          ctx.stroke();
          break;
        }

        case 'divider': {
          const isH = (el.orientation || 'horizontal') === 'horizontal';
          ctx.strokeStyle = `rgb(${el.r},${el.g},${el.b})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          if (isH) {
            ctx.moveTo(0, ay * s + 0.5);
            ctx.lineTo(_canvas.width, ay * s + 0.5);
          } else {
            ctx.moveTo(ax * s + 0.5, 0);
            ctx.lineTo(ax * s + 0.5, _canvas.height);
          }
          ctx.stroke();
          break;
        }

        case 'pips': {
          const pipCount = Math.max(1, el.count ?? 5);
          const pvPips = opts.previewValues?.[el.binding?.key];
          const filledN = pvPips !== undefined
            ? Math.max(0, Math.min(pipCount, Math.round(parseFloat(pvPips) || 0)))
            : Math.max(0, Math.min(pipCount, el.filled ?? 3));
          const ps = Math.max(1, el.pipSize ?? 4);
          const pg = Math.max(0, el.pipSpacing ?? 2);
          for (let i = 0; i < pipCount; i++) {
            const isFilled = i < filledN;
            if (!isFilled && !el.showEmpty) continue;
            ctx.fillStyle = isFilled
              ? `rgb(${el.r},${el.g},${el.b})`
              : `rgb(${el.emptyR ?? 50},${el.emptyG ?? 50},${el.emptyB ?? 50})`;
            ctx.fillRect((ax + i * (ps + pg)) * s, ay * s, ps * s, ps * s);
          }
          break;
        }

        case 'sparkline': {
          const slW = el.width ?? 40, slH = el.height ?? 12;
          const count = Math.max(1, el.barCount ?? 8);
          const spacing = el.barSpacing ?? 1;
          const barW = Math.max(1, Math.floor((slW - spacing * (count - 1)) / count));
          const rawVals = (el.previewData || '').split(',')
            .map(v => parseFloat(v.trim())).filter(n => !isNaN(n));
          while (rawVals.length < count) rawVals.push(0);
          const maxV = Math.max(...rawVals.slice(0, count), 0.001);
          const rx = ax * s, ry = ay * s;
          if (el.hasBg) {
            ctx.fillStyle = `rgb(${el.bgR ?? 30},${el.bgG ?? 30},${el.bgB ?? 30})`;
            ctx.fillRect(rx, ry, slW * s, slH * s);
          }
          ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
          for (let i = 0; i < count; i++) {
            const norm = Math.max(0, Math.min(1, rawVals[i] / maxV));
            const barH = Math.max(1, Math.round(slH * norm));
            const bx = rx + (barW + spacing) * i * s;
            const by = ry + (slH - barH) * s;
            ctx.fillRect(bx, by, barW * s, barH * s);
          }
          break;
        }

        case 'gauge': {
          const gw = (el.width ?? 32), gh = (el.height ?? 32);
          const cx = (ax + gw / 2) * s, cy = (ay + gh / 2) * s;
          const rx = (gw / 2) * s, ry = (gh / 2) * s;
          const lw = Math.max(1, (el.lineWidth ?? 3));
          const startDeg = el.startAngle ?? 135;
          const endDeg   = el.endAngle   ?? 45;
          // Arc sweep: from startDeg clockwise to endDeg (PIL convention)
          const totalSweep = ((endDeg - startDeg) + 360) % 360 || 360;
          const pvGauge = opts.previewValues?.[el.binding?.key];
          const pct = pvGauge !== undefined
            ? Math.max(0, Math.min(100, parseFloat(pvGauge) || 0)) / 100
            : Math.max(0, Math.min(100, el.previewPct ?? 65)) / 100;
          const fillSweep = totalSweep * pct;
          const toRad = deg => (deg - 90) * Math.PI / 180; // canvas 0=top, PIL 0=right → offset -90

          // Track arc
          if (el.hasTrack !== false) {
            ctx.beginPath();
            ctx.ellipse(cx, cy, rx - lw / 2, ry - lw / 2, 0, toRad(startDeg), toRad(startDeg + totalSweep), false);
            ctx.strokeStyle = `rgb(${el.trackR ?? 40},${el.trackG ?? 40},${el.trackB ?? 40})`;
            ctx.lineWidth = lw * s;
            ctx.stroke();
          }
          // Fill arc
          if (pct > 0) {
            ctx.beginPath();
            ctx.ellipse(cx, cy, rx - lw / 2, ry - lw / 2, 0, toRad(startDeg), toRad(startDeg + fillSweep), false);
            ctx.strokeStyle = `rgb(${el.r},${el.g},${el.b})`;
            ctx.lineWidth = lw * s;
            ctx.stroke();
          }
          // Centre label
          if (el.showLabel) {
            const gfinfo = FONT_MAP[el.font || 'four_by_six'] || FONT_MAP.four_by_six;
            const labelText = Math.round(pct * 100) + '%';
            ctx.font = `${gfinfo.sizePx * s}px ${gfinfo.family}`;
            ctx.fillStyle = `rgb(${el.labelR ?? 200},${el.labelG ?? 200},${el.labelB ?? 200})`;
            const ltw = ctx.measureText(labelText).width;
            ctx.fillText(labelText, cx - ltw / 2, cy + (gfinfo.sizePx * s) / 2);
          }
          break;
        }

        case 'marquee': {
          const finfo = FONT_MAP[el.font] || FONT_MAP.press_start;
          const text = el.text || 'Scrolling text';
          const tw = text.length * finfo.charW * s;
          const gap = (el.gap ?? 16) * s;
          const totalW = tw + gap;
          const tick = opts.animTick ?? 0;
          const speed = (el.scrollSpeed ?? 1) * 2;
          const scrolled = (tick * speed) % totalW;
          // left: text enters from right; right: text enters from left
          const startX = el.direction === 'right'
            ? scrolled - tw
            : matrixW * s - scrolled;
          ctx.font = `${finfo.sizePx * s}px ${finfo.family}`;
          ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
          // Clip to canvas width so text doesn't bleed outside
          ctx.save();
          ctx.beginPath();
          ctx.rect(0, ay * s - 1, matrixW * s, (finfo.sizePx + 2) * s);
          ctx.clip();
          for (let i = -1; i <= 2; i++) {
            ctx.fillText(text, startX + i * totalW, (ay + finfo.sizePx) * s);
          }
          ctx.restore();
          break;
        }

        case 'progress_bar': {
          const bw = el.barWidth ?? 60, bh = el.barHeight ?? 6;
          const pvPb = opts.previewValues?.[el.binding?.key];
          const pct = pvPb !== undefined
            ? Math.max(0, Math.min(100, parseFloat(pvPb) || 0)) / 100
            : Math.max(0, Math.min(100, el.previewPct ?? 65)) / 100;
          const rx = ax * s, ry = ay * s;
          if (el.hasBg) {
            ctx.fillStyle = `rgb(${el.bgR ?? 30},${el.bgG ?? 30},${el.bgB ?? 30})`;
            ctx.fillRect(rx, ry, bw * s, bh * s);
          }
          const fillW = Math.max(0, Math.round(bw * pct));
          if (fillW > 0) {
            ctx.fillStyle = `rgb(${el.r},${el.g},${el.b})`;
            ctx.fillRect(rx, ry, fillW * s, bh * s);
          }
          if (el.hasOutline) {
            ctx.strokeStyle = `rgb(${el.outR ?? 100},${el.outG ?? 100},${el.outB ?? 100})`;
            ctx.lineWidth = 1;
            ctx.strokeRect(rx, ry, bw * s, bh * s);
          }
          break;
        }
      }

      if (belowBreakpoint) {
        ctx.globalAlpha = 0.6;
        const bb = getBoundingBox(el, matrixW, matrixH);
        ctx.font = `${Math.max(8, s * 2)}px monospace`;
        ctx.fillStyle = '#facc15';
        ctx.fillText(`≥${el.minWidth}px`, bb.x * s, (bb.y + 4) * s);
      }
    } finally {
      ctx.restore();
    }
  }

  // ── Selection indicator ──────────────────────────────────────────────
  function _drawSelection(ctx, el, SCALE, matrixW, matrixH) {
    const bb = getBoundingBox(el, matrixW, matrixH);
    const PAD = 2, s = SCALE;
    const rx = bb.x * s - PAD, ry = bb.y * s - PAD;
    const rw = bb.w * s + PAD * 2, rh = bb.h * s + PAD * 2;

    ctx.save();
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 2]);
    ctx.strokeRect(rx, ry, rw, rh);
    ctx.setLineDash([]);

    if (el.xAnchor || el.yAnchor) {
      ctx.font = `${Math.max(7, s)}px sans-serif`;
      ctx.fillStyle = '#a78bfa';
      const anchorText = [
        el.xAnchor ? `x:${el.xAnchor[0]}` : '',
        el.yAnchor ? `y:${el.yAnchor[0]}` : '',
      ].filter(Boolean).join(' ');
      if (anchorText) ctx.fillText(anchorText, rx + 1, ry - 2);
    }

    // Resize handles: on rect, rounded rect, ellipse
    if (['rectangle', 'rounded_rectangle', 'ellipse', 'arc', 'gauge', 'sparkline'].includes(el.type)) {
      const handles = _getRectHandles(el, matrixW, matrixH);
      const HS = 5;
      ctx.fillStyle = 'white';
      ctx.strokeStyle = '#2563eb';
      ctx.lineWidth = 1;
      for (const pt of Object.values(handles)) {
        const hx = pt.x * s - HS / 2;
        const hy = pt.y * s - HS / 2;
        ctx.fillRect(hx, hy, HS, HS);
        ctx.strokeRect(hx, hy, HS, HS);
      }
    } else {
      // Corner dots for non-rectangle elements
      ctx.fillStyle = '#3b82f6';
      const HS = 4;
      for (const [hx, hy] of [
        [rx - HS / 2, ry - HS / 2], [rx + rw - HS / 2, ry - HS / 2],
        [rx - HS / 2, ry + rh - HS / 2], [rx + rw - HS / 2, ry + rh - HS / 2],
      ]) ctx.fillRect(hx, hy, HS, HS);
    }

    ctx.restore();
  }

  // ── Dimension tooltip while dragging ─────────────────────────────────
  function drawDragTooltip(ctx, el, SCALE, matrixW, matrixH) {
    const bb = getBoundingBox(el, matrixW, matrixH);
    const label = el.type === 'rectangle'
      ? `${el.width}×${el.height}`
      : `${bb.x},${bb.y}`;
    const s = SCALE;
    ctx.save();
    ctx.font = `${Math.max(9, s * 1.5)}px monospace`;
    const tw = ctx.measureText(label).width;
    const tx = bb.x * s, ty = (bb.y - 2) * s;
    ctx.fillStyle = 'rgba(0,0,0,0.7)';
    ctx.fillRect(tx - 2, ty - 10, tw + 4, 12);
    ctx.fillStyle = 'white';
    ctx.fillText(label, tx, ty);
    ctx.restore();
  }

  // ── Public API ───────────────────────────────────────────────────────

  function init(canvasEl) {
    _canvas = canvasEl;
    _ctx = canvasEl.getContext('2d');
  }

  function setGrid(show) { _showGrid = show; }

  function updateCanvasSize(matrixW, matrixH, SCALE) {
    if (!_canvas) return;
    _canvas.width = matrixW * SCALE;
    _canvas.height = matrixH * SCALE;
  }

  function render(elements, selectedId, matrixW, matrixH, SCALE, opts = {}) {
    if (!_ctx) return;
    const cW = matrixW * SCALE, cH = matrixH * SCALE;

    const bg = opts.bgColor;
    _ctx.fillStyle = bg ? `rgb(${bg.r},${bg.g},${bg.b})` : '#000';
    _ctx.fillRect(0, 0, cW, cH);

    if (_showGrid) {
      _ctx.strokeStyle = 'rgba(255,255,255,0.07)';
      _ctx.lineWidth = 0.5;
      for (let x = SCALE; x < cW; x += SCALE) {
        _ctx.beginPath(); _ctx.moveTo(x, 0); _ctx.lineTo(x, cH); _ctx.stroke();
      }
      for (let y = SCALE; y < cH; y += SCALE) {
        _ctx.beginPath(); _ctx.moveTo(0, y); _ctx.lineTo(cW, y); _ctx.stroke();
      }
    }

    for (const el of elements) _drawElement(_ctx, el, SCALE, matrixW, matrixH, opts);

    if (opts.showRuler) {
      _ctx.save();
      _ctx.fillStyle = 'rgba(255,255,255,0.08)';
      _ctx.fillRect(0, 0, cW, SCALE);             // top strip
      _ctx.fillRect(0, 0, SCALE, cH);             // left strip
      _ctx.strokeStyle = 'rgba(255,255,255,0.5)';
      _ctx.fillStyle = 'rgba(255,255,255,0.6)';
      _ctx.font = `${Math.max(5, SCALE - 1)}px monospace`;
      const step = SCALE >= 4 ? 8 : 16;
      for (let px = 0; px <= matrixW; px += step) {
        const cx = px * SCALE;
        const major = px % 32 === 0;
        _ctx.lineWidth = 0.5;
        _ctx.beginPath(); _ctx.moveTo(cx, 0); _ctx.lineTo(cx, major ? SCALE : SCALE * 0.5); _ctx.stroke();
        if (major && px > 0 && px < matrixW - 4) _ctx.fillText(String(px), cx + 1, SCALE - 1);
      }
      for (let py = 0; py <= matrixH; py += step) {
        const cy = py * SCALE;
        const major = py % 32 === 0;
        _ctx.beginPath(); _ctx.moveTo(0, cy); _ctx.lineTo(major ? SCALE : SCALE * 0.5, cy); _ctx.stroke();
        if (major && py > 0 && py < matrixH - 4) _ctx.fillText(String(py), 1, cy + SCALE - 1);
      }
      _ctx.restore();
    }

    if (opts.showGuides) {
      _ctx.save();
      _ctx.strokeStyle = 'rgba(255,60,60,0.45)';
      _ctx.lineWidth = 1;
      _ctx.setLineDash([4, 3]);
      const mx = Math.floor(cW / 2) + 0.5;
      const my = Math.floor(cH / 2) + 0.5;
      _ctx.beginPath(); _ctx.moveTo(mx, 0); _ctx.lineTo(mx, cH); _ctx.stroke();
      _ctx.beginPath(); _ctx.moveTo(0, my); _ctx.lineTo(cW, my); _ctx.stroke();
      _ctx.setLineDash([]);
      _ctx.restore();
    }

    const sel = selectedId != null ? elements.find(e => e.id === selectedId) : null;
    if (sel) {
      _drawSelection(_ctx, sel, SCALE, matrixW, matrixH);
      if (opts.showTooltip) drawDragTooltip(_ctx, sel, SCALE, matrixW, matrixH);
    }
  }

  return {
    init, render, setGrid, updateCanvasSize,
    hitTest, getBoundingBox, computeActualPos, resolveAnchor,
    getResizeHandle, getCursorForHandle,
    ELEMENT_DEFAULTS, FONT_MAP, DISPLAY_PRESETS,
  };
})();
