/*
 * core/registry.js -- the page lifecycle for HTMX-swapped partials.
 *
 * A partial marks its root element with data-page="<name>". A page module
 * (static/v3/js/pages/<name>.js) exports:
 *
 *   init(root, ctx)      wire the page up. Runs once per root element.
 *   destroy(root, ctx)   optional; undo anything `ctx.signal` does not.
 *
 * ctx is a per-mount object holding the shared services passed to
 * createRegistry({ context }) (boot.js passes `api` and `notify`) plus:
 *   ctx.root     the data-page element
 *   ctx.name     the page name
 *   ctx.signal   an AbortSignal aborted on destroy. Pass it to
 *                addEventListener(type, fn, { signal }) and to fetch(), and
 *                the listeners go away and requests are cancelled with no
 *                bookkeeping in the page.
 *   ctx.state    a plain object the page may keep its own state in
 *
 * Mounting is idempotent: a root that is already mounted is never initialised
 * twice, which is the guarantee the old inline <script> blocks could not give
 * (htmx-config.js re-ran them on every swap).
 *
 * Wiring (start()):
 *   htmx:beforeSwap  destroys every mounted page inside the swap target, unless
 *                    the swap was vetoed (detail.shouldSwap false). Listening
 *                    on `document` rather than `body` puts this after the
 *                    body-level handlers in htmx-config.js that can veto it.
 *   htmx:afterSwap   destroys any mounted page whose root has left the
 *                    document (a swap styled outerHTML, a panel removed by
 *                    Alpine), then mounts every data-page root not yet mounted.
 *   refresh()        the same as afterSwap, for content inserted without htmx
 *                    (base.html's loadPartialDirect fallback calls it).
 *
 * No DOM globals are read at import time, so node tests can import this file
 * and hand createRegistry() a jsdom document.
 */

export const PAGE_ATTRIBUTE = 'data-page';

/**
 * @param {object} [options]
 * @param {Document} [options.document]  the document to wire (default: globalThis.document)
 * @param {object} [options.context]     services copied onto every page's ctx
 * @param {{error: Function}} [options.logger]
 */
export function createRegistry(options = {}) {
    const doc = options.document || globalThis.document;
    const logger = options.logger || console;
    const services = options.context || {};
    // The document's own AbortController: an element only accepts a signal
    // from its own realm (it matters for jsdom in the tests, not in a browser).
    const Controller = (doc && doc.defaultView && doc.defaultView.AbortController) || globalThis.AbortController;
    const selector = '[' + PAGE_ATTRIBUTE + ']';

    /** name -> { load: () => Promise<module> | module, module: object|null } */
    const definitions = new Map();
    /** root element -> mount entry */
    const mounted = new Map();
    let started = false;

    function register(name, moduleOrLoader) {
        if (typeof name !== 'string' || !name) {
            throw new TypeError('registerPage: a page needs a non-empty name');
        }
        if (definitions.has(name)) {
            throw new Error('registerPage: "' + name + '" is already registered');
        }
        const isLoader = typeof moduleOrLoader === 'function';
        if (!isLoader && !(moduleOrLoader && typeof moduleOrLoader.init === 'function')) {
            throw new TypeError('registerPage: "' + name + '" needs a module with init(), or a loader function');
        }
        definitions.set(name, {
            load: isLoader ? moduleOrLoader : null,
            module: isLoader ? null : moduleOrLoader,
        });
        // A partial may already be on the page (it arrived before this page
        // was registered); mount it now rather than waiting for the next swap.
        if (started) scan(doc);
    }

    async function resolve(definition) {
        if (!definition.module) {
            const loaded = await definition.load();
            // `import()` resolves to a namespace object; a loader may also
            // return the module object itself.
            definition.module = loaded && loaded.default && typeof loaded.init !== 'function'
                ? loaded.default : loaded;
            if (!definition.module || typeof definition.module.init !== 'function') {
                throw new TypeError('page module has no init()');
            }
        }
        return definition.module;
    }

    function mount(root) {
        const existing = mounted.get(root);
        if (existing) return existing.ready;
        const name = root.getAttribute(PAGE_ATTRIBUTE);
        const definition = definitions.get(name);
        if (!definition) {
            // Not an error: a page module may register later (scan again then).
            return Promise.resolve(false);
        }
        const controller = new Controller();
        const ctx = Object.assign({}, services,
                                  { root: root, name: name, signal: controller.signal, state: {} });
        const entry = { name: name, root: root, ctx: ctx, controller: controller,
                        module: null, initialised: false, destroyed: false, ready: null };
        mounted.set(root, entry);
        entry.ready = resolve(definition).then(function(module) {
            // Destroyed (swapped away) while the module was still loading.
            if (entry.destroyed) return false;
            entry.module = module;
            module.init(root, ctx);
            entry.initialised = true;
            return true;
        }).catch(function(error) {
            logger.error('[LEDMatrix.pages] ' + name + ' failed to start:', error);
            // Leave it mounted (but inert) so it is not retried on every swap;
            // the next swap of that partial gets a fresh root and a fresh try.
            return false;
        });
        return entry.ready;
    }

    function unmount(root) {
        const entry = mounted.get(root);
        if (!entry) return false;
        mounted.delete(root);
        entry.destroyed = true;
        if (entry.initialised && typeof entry.module.destroy === 'function') {
            try {
                entry.module.destroy(root, entry.ctx);
            } catch (error) {
                logger.error('[LEDMatrix.pages] ' + entry.name + ' destroy failed:', error);
            }
        }
        // After destroy(), so the page can still use its signal while tearing down.
        entry.controller.abort();
        return true;
    }

    function rootsIn(container) {
        if (!container || typeof container.querySelectorAll !== 'function') return [];
        const roots = Array.from(container.querySelectorAll(selector));
        if (typeof container.matches === 'function' && container.matches(selector)) {
            roots.unshift(container);
        }
        return roots;
    }

    /** Mount every data-page root inside `container` (default: the document). */
    function scan(container) {
        return Promise.all(rootsIn(container || doc).map(mount));
    }

    /** Destroy every mounted page whose root is `container` or inside it. */
    function release(container) {
        if (!container || typeof container.contains !== 'function') return 0;
        let count = 0;
        Array.from(mounted.keys()).forEach(function(root) {
            if (container.contains(root) && unmount(root)) count++;
        });
        return count;
    }

    /** Destroy every mounted page whose root is no longer in the document. */
    function sweep() {
        let count = 0;
        Array.from(mounted.keys()).forEach(function(root) {
            if (!root.isConnected && unmount(root)) count++;
        });
        return count;
    }

    function refresh() {
        sweep();
        return scan(doc);
    }

    function onBeforeSwap(event) {
        const detail = event.detail || {};
        if (detail.shouldSwap === false) return;
        release(detail.target);
    }

    function onAfterSwap() {
        refresh();
    }

    function start() {
        if (started) return refresh();
        started = true;
        doc.addEventListener('htmx:beforeSwap', onBeforeSwap);
        doc.addEventListener('htmx:afterSwap', onAfterSwap);
        return refresh();
    }

    function stop() {
        if (!started) return;
        started = false;
        doc.removeEventListener('htmx:beforeSwap', onBeforeSwap);
        doc.removeEventListener('htmx:afterSwap', onAfterSwap);
        Array.from(mounted.keys()).forEach(unmount);
    }

    /** Snapshot for debugging and tests: [{ name, root, initialised }]. */
    function list() {
        return Array.from(mounted.values()).map(function(entry) {
            return { name: entry.name, root: entry.root, initialised: entry.initialised };
        });
    }

    return {
        register: register,
        mount: mount,
        unmount: unmount,
        scan: scan,
        release: release,
        sweep: sweep,
        refresh: refresh,
        start: start,
        stop: stop,
        list: list,
        has: function(name) { return definitions.has(name); },
    };
}
