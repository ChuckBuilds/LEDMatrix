/*
 * core/visibility.js -- run a page's timers and polling only while the page
 * is on screen: its tab is the active tab AND the browser tab is visible.
 *
 * Each mounted page gets its own handle as ctx.visibility (the registry asks
 * boot.js for it on every mount; see createRegistry's `mountContext`):
 *
 *   ctx.visibility.whileVisible(start, stop)
 *       start() runs when the page comes on screen, stop() when it leaves.
 *       start() should refresh at once: it also runs when the page mounts
 *       already on screen. Returns a function that ends the registration
 *       (running stop() first if it is running).
 *   ctx.visibility.every(ms, fn)
 *       fn() at once and then every `ms` while on screen; the interval is
 *       cleared while hidden and restarted (with an immediate fn()) when the
 *       page is back. Returns the same kind of end function.
 *   ctx.visibility.isVisible()
 *       true while the page is on screen.
 *
 * Everything a page registered ends when the page is destroyed (its
 * ctx.signal aborts, after destroy()), so a page needs no teardown for it:
 * a swapped-out partial leaves no interval behind.
 *
 * Which tab: the page's name (ctx.name), or forPage(ctx, { tab }) when a
 * page's tab is named differently.
 *
 * Where the answer comes from: window.LEDVisibility (app-shell.js), read at
 * call time. It owns the one notion of "active tab" (Alpine's activeTab plus
 * the ledmatrix:tab-changed event) and already pauses the SSE streams, so
 * the classic partials that still call it and the page modules agree. Each
 * registration here takes its own LEDVisibility key, so two pages (or two
 * timers on one page) never replace each other. Without LEDVisibility (a
 * page outside base.html) the browser tab's visibility alone decides.
 *
 * No DOM globals are read at import time, so node tests can import this file
 * and hand createVisibility() a jsdom window.
 */

/**
 * @param {object} [options]
 * @param {Window} [options.window]   default: globalThis
 * @param {Function} [options.tracker]  () => an LEDVisibility-shaped object or null
 *                                    (default: window.LEDVisibility at call time)
 * @param {{error: Function}} [options.logger]
 */
export function createVisibility(options = {}) {
    const win = options.window || globalThis;
    const doc = win.document;
    const logger = options.logger || win.console || console;
    const tracker = options.tracker || function() { return win.LEDVisibility || null; };
    let sequence = 0;

    function call(fn, label) {
        try {
            fn();
        } catch (error) {
            logger.error('[LEDMatrix.visibility] ' + label + ' failed:', error);
        }
    }

    /**
     * Without a tracker: follow document.hidden only. Same contract as
     * LEDVisibility.onActive: start/stop on change, returns an unregister fn.
     */
    function onDocumentVisible(start, stop) {
        let running = false;
        function evaluate() {
            const want = !doc.hidden;
            if (want === running) return;
            running = want;
            call(want ? start : stop, want ? 'start' : 'stop');
        }
        doc.addEventListener('visibilitychange', evaluate);
        evaluate();
        return function() {
            doc.removeEventListener('visibilitychange', evaluate);
            if (running) { running = false; call(stop, 'stop'); }
        };
    }

    /**
     * The handle for one mounted page. Everything it registers ends when
     * ctx.signal aborts.
     */
    function forPage(ctx, pageOptions = {}) {
        const tab = pageOptions.tab || ctx.name;
        const signal = ctx.signal;
        const live = new Set();

        function whileVisible(start, stop) {
            if (typeof start !== 'function' || typeof stop !== 'function') {
                throw new TypeError('visibility.whileVisible(start, stop) needs two functions');
            }
            if (signal && signal.aborted) return function() {};
            const t = tracker();
            const key = 'page:' + ctx.name + ':' + (++sequence);
            const unregister = t
                ? t.onActive(tab, start, stop, key)
                : onDocumentVisible(start, stop);
            let ended = false;
            const end = function() {
                if (ended) return;
                ended = true;
                live.delete(end);
                if (typeof unregister === 'function') call(unregister, key + ' stop');
            };
            live.add(end);
            return end;
        }

        function every(ms, fn) {
            if (!(ms > 0) || typeof fn !== 'function') {
                throw new TypeError('visibility.every(ms, fn) needs a positive interval and a function');
            }
            let timer = null;
            function stop() {
                if (timer !== null) {
                    win.clearInterval(timer);
                    timer = null;
                }
            }
            return whileVisible(function() {
                stop();
                timer = win.setInterval(function() { call(fn, 'every(' + ms + ')'); }, ms);
                fn();
            }, stop);
        }

        function isVisible() {
            const t = tracker();
            return t ? t.isActive(tab) : !doc.hidden;
        }

        if (signal) {
            signal.addEventListener('abort', function() {
                Array.from(live).forEach(function(end) { end(); });
            }, { once: true });
        }

        return Object.freeze({ tab: tab, whileVisible: whileVisible, every: every, isVisible: isVisible });
    }

    return { forPage: forPage };
}
