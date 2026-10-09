/*
 * Tailwind config for the web UI (static/v3/tailwind.css).
 *
 * Built by scripts/build_css.py with the pinned standalone CLI; the output is
 * committed and CI fails if it is stale. After changing a template, a static
 * JS file or this config, run `python3 scripts/build_css.py` and commit
 * static/v3/tailwind.css with your change.
 *
 * tailwind.css holds the utilities; app.css (loaded after it) holds the
 * theme tokens, base element styles, components and dark-theme overrides.
 * The theme below keeps the values app.css used to hand-write, so moving to
 * a real build restyled nothing that already worked.
 */
const colors = require('tailwindcss/colors');

module.exports = {
  content: {
    relative: true,
    files: [
      '../templates/v3/**/*.html',
      '../static/v3/*.js',
      '../static/v3/js/**/*.js',
      '!../static/v3/js/*.min.js',
      '../blueprints/**/*.py',
    ],
  },

  // Class names assembled at runtime, which the scanner can't see.
  safelist: [
    // tools.html showResult() and the power-status badge build
    // `bg-${color}-50`, `text-${color}-800`, ... from green/red/yellow.
    { pattern: /^bg-(green|red|yellow)-(50|100)$/ },
    { pattern: /^border-(green|red|yellow)-200$/ },
    { pattern: /^text-(green|red|yellow)-(600|700|800)$/ },
    // Hand-written in app.css before this build and unused by core since.
    // Third-party plugin widgets render into the page and may use them, so
    // the generated set stays a superset of what used to work.
    'grid-cols-3', 'grid-cols-4', 'gap-x-3', 'gap-x-4', 'hover:border-gray-300',
    'sm:hidden', 'sm:inline', 'sm:max-w-4xl', 'md:flex',
    'lg:grid-cols-5', 'lg:grid-cols-6', 'lg:gap-x-6',
    'xl:grid-cols-5', 'xl:grid-cols-6', 'xl:grid-cols-7', 'xl:grid-cols-8', 'xl:gap-x-8',
    // Used only by the removed js/utils/error_handler.js modal; kept for the
    // same reason (soccer-scoreboard's custom-leagues widget uses max-h-48).
    'align-bottom', 'bg-opacity-75', 'leading-6', 'list-inside', 'max-h-48',
    'pb-20', 'pt-5', 'transition-opacity', 'focus:ring-indigo-500',
    'sm:align-middle', 'sm:flex', 'sm:flex-row-reverse', 'sm:h-10', 'sm:items-start',
    'sm:max-w-lg', 'sm:ml-3', 'sm:ml-4', 'sm:mt-0', 'sm:mx-0', 'sm:my-8', 'sm:p-0',
    'sm:p-6', 'sm:pb-4', 'sm:text-left', 'sm:w-10', 'sm:w-auto', 'sm:w-full',
  ],

  // Rules app.css defines on purpose and Tailwind must not override. Tailwind
  // would show these on every focus; app.css shows them for keyboard focus
  // only (:focus-visible), so a mouse click doesn't flash a ring.
  blocklist: [
    'focus:outline-none',
    'peer-focus:outline-none',
    'peer-focus:ring-4',
    'peer-focus:ring-blue-300',
  ],

  // The app switches themes with <html data-theme="dark">, not a class.
  darkMode: ['selector', '[data-theme="dark"]'],

  // app.css owns the base styles; preflight would reset headings, lists and
  // form controls the UI already styles. The border reset utilities rely on
  // is in app.input.css.
  corePlugins: { preflight: false },

  theme: {
    extend: {
      // Light-mode gray text is one step darker than stock Tailwind for
      // contrast (text-gray-400 reads as gray-500, and so on).
      textColor: {
        gray: {
          ...colors.gray,
          400: colors.gray[500],
          500: colors.gray[600],
          600: colors.gray[700],
        },
        green: { ...colors.green, 600: colors.emerald[600] },
        yellow: { ...colors.yellow, 300: colors.amber[300] },
      },
      // Solid green/yellow buttons and dots use the deeper emerald/amber
      // shades, which keep white text readable.
      backgroundColor: {
        green: {
          ...colors.green,
          500: colors.emerald[500],
          600: colors.emerald[600],
          700: colors.emerald[700],
        },
        yellow: {
          ...colors.yellow,
          500: colors.amber[500],
          600: colors.amber[600],
          700: colors.amber[700],
        },
      },
      fontSize: {
        xs: ['0.75rem', '1.4'],
        sm: ['0.875rem', '1.5'],
        base: ['1rem', '1.5'],
        md: ['1rem', '1.5rem'],
        lg: ['1.125rem', '1.75'],
        xl: ['1.25rem', '1.75'],
        '2xl': ['1.5rem', '2'],
        '4xl': ['2.25rem', '2.5'],
      },
      // Shadows follow the theme tokens in app.css (:root / [data-theme]).
      boxShadow: {
        sm: 'var(--shadow-sm)',
        DEFAULT: 'var(--shadow)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
      },
      // `transition` animates only compositor-friendly properties.
      transitionProperty: {
        DEFAULT: 'transform, opacity, color, border-color',
      },
      // The gap a focus ring leaves matches the surface, in both themes.
      ringOffsetColor: {
        DEFAULT: 'var(--color-surface, #fff)',
      },
    },
  },
};
