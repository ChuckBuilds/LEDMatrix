/*
 * Tailwind config for static/v3/plugin-frame.css: the stylesheet
 * /v3/plugin-ui/<plugin>/web-ui/<file> links for a plugin's web_ui/ fragment
 * (shown in an iframe on the plugin's config tab).
 *
 * Fragments used to load Tailwind v2.2.19 from a CDN, which fails when the Pi
 * is in AP mode with no internet. Their markup lives in plugin repos and
 * can't be scanned here, so instead of content globs this file safelists the
 * common utility families at the values the v2 CDN had. A fragment that
 * needs something rarer should ship it in its own <style>.
 *
 * Patterns are regex literals, so the colour families (gray red yellow green
 * blue indigo purple pink) and the spacing scale are spelled out in each
 * pattern that uses them; change every copy together.
 *
 * Rebuild with `python3 scripts/build_css.py` after changing this file.
 */
const colors = require('tailwindcss/colors');

module.exports = {
  content: {
    relative: true,
    // The route's own wrapper markup; everything else is safelisted.
    files: ['../blueprints/pages_v3.py'],
  },
  safelist: [
    // Colour
    {
      pattern: /^(bg|text|border)-(gray|red|yellow|green|blue|indigo|purple|pink)-(50|100|200|300|400|500|600|700|800|900)$/,
      variants: ['hover'],
    },
    { pattern: /^(bg|text|border)-(white|black|transparent|current)$/, variants: ['hover'] },
    { pattern: /^(ring|placeholder)-(gray|red|yellow|green|blue|indigo|purple|pink)-(300|400|500|600)$/, variants: ['focus'] },
    { pattern: /^border-(gray|red|yellow|green|blue|indigo|purple|pink)-(300|400|500|600)$/, variants: ['focus'] },
    { pattern: /^bg-opacity-(0|25|50|75|100)$/ },
    { pattern: /^opacity-(0|25|50|75|100)$/, variants: ['hover', 'disabled'] },

    // Spacing and sizing
    { pattern: /^(p|px|py|pt|pr|pb|pl|m|mx|my|mt|mr|mb|ml)-(0|0\.5|1|1\.5|2|2\.5|3|4|5|6|8|10|12|16|20|24)$/ },
    { pattern: /^(m|mx|my|mt|mr|mb|ml)-auto$/ },
    { pattern: /^(space-x|space-y|gap|gap-x|gap-y)-(0|0\.5|1|1\.5|2|2\.5|3|4|5|6|8|10|12|16|20|24)$/ },
    { pattern: /^(w|h)-(0|1|2|3|4|5|6|8|10|12|16|20|24|32|40|48|56|64|72|80|96|auto|full|screen|px|1\/2|1\/3|2\/3|1\/4|3\/4)$/ },
    { pattern: /^min-(w|h)-(0|full|screen)$/ },
    { pattern: /^max-w-(xs|sm|md|lg|xl|2xl|3xl|4xl|5xl|6xl|7xl|full|screen-sm|screen-md|screen-lg|none)$/ },
    { pattern: /^max-h-(32|48|64|96|full|screen)$/ },

    // Layout
    { pattern: /^(block|inline-block|inline|flex|inline-flex|grid|inline-grid|table|table-row|table-cell|hidden|contents)$/, variants: ['sm', 'md', 'lg'] },
    { pattern: /^flex-(1|auto|initial|none|row|row-reverse|col|col-reverse|wrap|nowrap|grow|shrink|grow-0|shrink-0)$/, variants: ['sm', 'md', 'lg'] },
    { pattern: /^(grow|shrink|grow-0|shrink-0)$/ },
    { pattern: /^(items|content)-(start|end|center|baseline|stretch|between)$/ },
    { pattern: /^(justify|self)-(start|end|center|between|around|evenly|auto|stretch)$/ },
    { pattern: /^grid-cols-(1|2|3|4|5|6|12)$/, variants: ['sm', 'md', 'lg'] },
    { pattern: /^col-span-(1|2|3|4|5|6|12|full)$/, variants: ['sm', 'md', 'lg'] },
    { pattern: /^(static|relative|absolute|fixed|sticky)$/ },
    { pattern: /^(inset|top|right|bottom|left)-(0|1|2|3|4|auto|full)$/ },
    { pattern: /^inset-(x|y)-0$/ },
    { pattern: /^z-(0|10|20|30|40|50|auto)$/ },
    { pattern: /^(overflow|overflow-x|overflow-y)-(auto|hidden|visible|scroll)$/ },
    { pattern: /^(float-left|float-right|clear-both|mx-auto|container|sr-only|not-sr-only)$/ },
    { pattern: /^object-(contain|cover|center)$/ },

    // Typography
    { pattern: /^text-(xs|sm|base|lg|xl|2xl|3xl|4xl)$/, variants: ['sm', 'md'] },
    { pattern: /^text-(left|center|right|justify)$/ },
    { pattern: /^font-(sans|serif|mono|light|normal|medium|semibold|bold|extrabold)$/ },
    { pattern: /^(uppercase|lowercase|capitalize|normal-case|italic|not-italic|underline|line-through|no-underline|truncate|break-words|break-all)$/, variants: ['hover'] },
    { pattern: /^whitespace-(normal|nowrap|pre|pre-line|pre-wrap)$/ },
    { pattern: /^leading-(none|tight|snug|normal|relaxed|loose|4|5|6|7|8)$/ },
    { pattern: /^tracking-(tighter|tight|normal|wide|wider|widest)$/ },
    { pattern: /^(list-none|list-disc|list-decimal|list-inside|list-outside)$/ },
    { pattern: /^align-(top|middle|bottom|baseline)$/ },

    // Borders, effects, interaction
    { pattern: /^border(-0|-2|-4|-t|-b|-l|-r|-t-0|-b-0|-t-2|-b-2|-l-4)?$/ },
    { pattern: /^border-(solid|dashed|dotted|none)$/ },
    { pattern: /^divide-(x|y)$/ },
    { pattern: /^divide-(gray|red|yellow|green|blue|indigo|purple|pink)-(100|200|300)$/ },
    { pattern: /^rounded(-none|-sm|-md|-lg|-xl|-2xl|-full)?$/ },
    { pattern: /^rounded-(t|b|l|r)(-md|-lg)?$/ },
    { pattern: /^shadow(-sm|-md|-lg|-xl|-2xl|-inner|-none)?$/, variants: ['hover'] },
    { pattern: /^(ring|ring-0|ring-1|ring-2|ring-4|ring-offset-2|outline-none)$/, variants: ['focus'] },
    { pattern: /^(transition|transition-colors|transition-opacity|transition-all|transform)$/ },
    { pattern: /^(duration|ease)-(150|200|300|in|out|in-out)$/ },
    { pattern: /^(animate-spin|animate-pulse)$/ },
    { pattern: /^cursor-(pointer|default|not-allowed|move|wait)$/ },
    { pattern: /^(select-none|select-all|pointer-events-none|pointer-events-auto|resize|resize-none|resize-y|appearance-none)$/ },
  ],
  theme: {
    extend: {
      // Tailwind v2's default palette, which fragments were written against:
      // its green, yellow and purple were v3's emerald, amber and violet.
      colors: {
        green: colors.emerald,
        yellow: colors.amber,
        purple: colors.violet,
      },
    },
  },
};
