# Web UI Technical Audit — September 2026

Scope: `web_interface/` (Flask + HTMX + Alpine, `templates/v3/`, `static/v3/`).
Method: Impeccable design detector, code review (accessibility; performance,
theming, responsive), and a live pass on the running app at desktop and
375px mobile widths in light and dark themes. Severe claims were verified
against the live page; one was rejected (see below). No code was changed.

Product context: see [`PRODUCT.md`](../../PRODUCT.md).

## Health score: 8/20 (Poor)

| # | Dimension | Score | Key finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 2 | Focus rings never render; modals have no dialog semantics or focus management |
| 2 | Performance | 2 | ~1.2 MB JS (≈250 KB gzip) on every page; SSE streams and polling never pause |
| 3 | Responsive | 2 | Mobile drawer works; header title wraps to 3 lines; many ~24px touch targets |
| 4 | Theming | 1 | Tokens exist but hex dominates; dark mode is a class-by-class patch with leaks |
| 5 | Implementation integrity | 1 | Templates use Tailwind classes that don't exist in the stylesheet |

## Implementation integrity verdict: fail

There is no Tailwind build. `static/v3/app.css` is a hand-written subset of
Tailwind, while templates and JS are authored as if full Tailwind were loaded.

- **333 of 516 utility class names used have no CSS rule** (2,582 uses),
  confirmed against the live stylesheets. Top offenders: `border` (250),
  `text-gray-700` (183), `block` (148), `mr-1` (127), `hidden` (79),
  `py-1`, `text-blue-600`, `px-2`, `text-center`, `hover:bg-blue-700`,
  `divide-y`, `uppercase`, `font-mono`.
- **`.hidden` has never existed in `app.css`**, so the 145
  `classList.add/remove/toggle('hidden')` calls across 26 files do nothing.
  Visible proof: the header shows both the moon and sun theme icons.
- **15 classes are defined only under `[data-theme="dark"]`** (e.g.
  `bg-blue-50`, `bg-red-50`, `bg-yellow-50`, `border-blue-200`,
  `text-red-700`), so tinted notice boxes are unstyled in light mode.
- Visible damage: the Getting Started checklist (`partials/overview.html:96-117`)
  renders native gray outset buttons in both themes; 32 visible buttons on
  the Plugin Manager page render with default browser chrome; search icons
  overlap inputs; error/diff modal backdrops are transparent
  (`bg-gray-500 bg-opacity-75` undefined).

Other drift:
- Four competing `showNotification` definitions (`app.js:6`,
  `app-shell.js:2464`, `widgets/notification.js:298`, `partials/fonts.html:249`)
  — the winner depends on load order — plus 53 `alert()`/`confirm()` calls.
- At least four modal implementations (on-demand modal in `base.html:1032`,
  Tailwind-UI style in `error_handler.js`/`diff_viewer.js`, `.jfm-*`/`.pfm-*`
  with injected CSS, ad-hoc modals in `plugins_manager.js`).
- `.btn` mixed with ~90 hand-assembled color-utility button combos.
- SSE wiring duplicated in `app-shell.js:5-60` and `app.js:186-205`.

## Findings by severity

### P0

**Undefined utility layer** (above). Every show/hide toggle and every layout
built from missing classes silently fails; root cause of most visual bugs.
Fix: replace the hand-rolled subset with a real, purged Tailwind build
(with dark-mode variants), or at minimum define the high-use missing classes
(`hidden`, `border`, `block`, spacing/text utilities) and a button reset.
→ `/impeccable harden`

### P1

- **Focus rings never render.** `focus:ring-2` (`app.css:289-291`) references
  `--tw-ring-inset` and `--tw-ring-offset-width`, which are never defined, so
  the `box-shadow` is invalid. `focus:outline-none` (18 uses) does remove the
  outline. `peer-focus:ring-4` has no rule, so the plugin enable toggle
  (`plugins_manager.js:1586-1593`, `sr-only` checkbox) shows no focus.
  WCAG 2.4.7.
- **Modals lack dialog semantics.** Only `json-file-manager.js` has
  `role="dialog"`/`aria-modal`/Escape/initial focus; none trap focus or
  return it. On-demand (`base.html:1032`), error (`error_handler.js:164-205`),
  diff (`diff_viewer.js:211-214`), plugin file manager
  (`plugin-file-manager.js:372-392, 576-599`), array-table editor
  (`array-table.js:460-467`) have none of it. WCAG 2.1.2 / 4.1.2.
- **Unnamed controls.** ~16 icon-only buttons with no accessible name, e.g.
  `base.html:1036`, `plugins.html:173,210`, `plugin_config.html:485,656`,
  `number-input.js:102,129`, `text-input.js:120`, `date-picker.js:95`,
  `time-picker.js:100`, `password-input.js:141`. ~115 of 245 form fields have
  no label (hotspots: `plugin_config.html` 19, `starlark_config.html` 14,
  `plugins.html` 11); confirmed live on the 11 store search/sort/filter inputs.
- **Captive WiFi setup page** (first-run surface): `#msg` status has no live
  region, and `outline:none` is replaced by a 15%-alpha shadow
  (`captive_setup.html:16,48`).
- **Background traffic never stops.** `/stream/stats` and `/stream/display`
  SSE stay open on every tab (display frames push with no preview visible).
  Tab timers keep running after leaving the tab (`display.html:1046` 5s,
  `logs.html:222` 5s, `tools.html:987` 15s, `plugins_manager.js:1882` 15s,
  update check `base.html:1196` 30min). Only `tools.html:999` checks
  `visibilitychange`. Costly on a Pi Zero 2 W.
- **Page weight.** 47 script tags on every page, including all 33 widgets
  (`base.html:984-1018`). `app-shell.js` (177 KB) is render-blocking
  (`base.html:956`); `plugins_manager.js` is 277 KB.
- **Dark mode leaks.** `plugin-file-manager.js` (53 hex) and
  `json-file-manager.js` (63 hex, e.g. `.jfm-modal-box{background:#fff}`)
  inject CSS that ignores `data-theme`; `.form-control` hard-codes
  `#fff`/`#111827` (`app.css:668-671`). `app.css` has 186 hex + 46 rgb
  literals vs 94 `var(--…)` uses.

### P2

- Toasts: `role="alert"` inside an `aria-live="polite"` container
  (`notification.js:78,156`) → double/assertive announcements; auto-dismiss 4s.
- `prefers-reduced-motion` covers 3 animations; ~106 `animate-pulse`/`fa-spin`
  uses, `modalSlideIn`, and toast slides ignore it.
- Mobile: header title wraps to three lines and spills out of the header;
  ~33 plugin-card buttons are `text-xs px-2 py-1` (~24px); `#logs-container`
  forced to 400/350px with `!important` (`app.css:399-411`).
- Logs panel contrast: `text-gray-400` on `bg-gray-900` ≈ 3.9:1
  (`logs.html:75,86`).
- Three unnamed nested `<nav>` landmarks (`base.html:474,477,548`); no skip link.
- Plugin lists fully rebuilt via `innerHTML` on every filter change
  (`plugins_manager.js:1554, 3784, 3993, 4389, 5905`); `logs.html:225` adds a
  reflow-forcing resize listener on every partial load.

### P3

- No `loading="lazy"` on images; Font Awesome `font-display:block`.
- Unpinned `alpinejs@3.x.x` unpkg fallback (`base.html:241`).
- `widgets/example-color-picker.js` is not loaded anywhere.
- Detector: 3px accent stripe on `.plugin-card::before` (`app.css:721`).

## Verified and rejected

- **"Static assets are never cache-busted" (raised as P0): false.**
  `app.py:491` (`@app.url_defaults add_static_version`) appends file mtime as
  `?v=` to every static URL; the live HTML confirms it. The manual
  `?v=20260307` on two script tags is merely redundant.
- Light-mode gray text contrast is mostly fine: `app.css` remaps grays darker
  (4.8–10:1).
- Detector `gray-on-color` hits at `app.css:84,285` and `broken-image` hits
  (JS-populated `src`) are not real rendered issues.

## What works

- Theme set before first paint, follows OS preference, `data-theme` + tokens.
- Mobile drawer: Escape closes it, focus returns to the hamburger, 44px rows.
- `aria-current="page"` on nav tabs; real `<header>` and `<main>`.
- Status colors always paired with text; nearly all images have alt text.
- `toggle-switch.js` uses `role="switch"`; vendor assets self-hosted.

## Open decisions (block the P0 fix approach)

Recorded as undecided in `PRODUCT.md`:
- Must the UI work fully offline (no CDN fallbacks)?
- Is a Node/CSS build step acceptable for contributors?
- Is WCAG 2.2 AA a formal requirement?

## Recommended order

1. **[P0] `/impeccable harden`** — fix the utility layer (real Tailwind build
   or define missing classes + button reset).
2. **[P1] `/impeccable harden`** — focus-ring variables and `peer-focus`;
   one shared accessible modal helper; name icon buttons and label fields;
   live region on the captive page.
3. **[P1] `/impeccable optimize`** — pause SSE/timers on hidden tab or page;
   load widget scripts on demand.
4. **[P1] `/impeccable colorize`** — move file-manager CSS and `.form-control`
   onto theme tokens.
5. **[P2] `/impeccable adapt`** — header wrap, touch targets, log height.
6. **[P2] `/impeccable animate`** — reduced-motion alternatives.
7. **`/impeccable polish`** — final pass.
