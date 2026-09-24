// window.LEDEscape, taken verbatim from web_interface/static/v3/js/app-early.js.
//
// Scripts that the suites evaluate on their own (a slice of plugins_manager.js,
// a widget file) call window.LEDEscape, which the page defines in app-early.js
// before anything else runs. `source` is that definition, for a jsdom <script>;
// install(win) evaluates it onto a plain object standing in for window.
const fs = require('fs');
const path = require('path');

const APP_EARLY = path.resolve(__dirname, '../../web_interface/static/v3/js/app-early.js');
const text = fs.readFileSync(APP_EARLY, 'utf8');
const start = text.indexOf('window.LEDEscape = (function() {');
const endMark = '})();';
const end = text.indexOf(endMark, start);
if (start < 0 || end < 0) {
  console.error('FAIL: cannot find the window.LEDEscape definition in app-early.js');
  process.exit(1);
}
const source = text.slice(start, end + endMark.length);

function install(win) {
  // eslint-disable-next-line no-new-func
  new Function('window', source)(win);
  return win.LEDEscape;
}

module.exports = { source, install };
