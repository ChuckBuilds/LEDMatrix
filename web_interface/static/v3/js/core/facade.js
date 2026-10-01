/*
 * core/facade.js -- window.LEDMatrix, the one global the module code adds.
 *
 *   LEDMatrix.api            core/api.js: get/post/put/del against /api/v3
 *   LEDMatrix.pages          the page registry: register(name, moduleOrLoader),
 *                            refresh(), list()
 *   LEDMatrix.notify(m, t)   window.showNotification, looked up at call time
 *   LEDMatrix.escape         window.LEDEscape (app-early.js)
 *   LEDMatrix.widgets        window.LEDMatrixWidgets (the widget registry)
 *   LEDMatrix.deprecate(...) keep an old window.* name working (see below)
 *
 * Plugins and third-party pages should reach the interface through this
 * object. The classic scripts still define their own window.* names; as each
 * one moves into a module, its old name stays as an alias made with
 * deprecate(), which warns once in the console and forwards to the new code.
 * Nothing is removed until a release announces it.
 *
 * escape, widgets and notify are read through at call time on purpose: the
 * classic scripts that define them are deferred and may load after this
 * module, and a plugin may replace showNotification.
 */

export const FACADE_VERSION = 1;

/**
 * Define window[name] as a deprecated alias of `target`.
 *
 * A function target is wrapped: the first call logs one console warning
 * naming the replacement, and every call forwards to `target`. Any other
 * value becomes a read-only getter with the same one-time warning.
 * An existing non-configurable property is left alone (returns false).
 */
export function defineDeprecatedAlias(win, name, target, replacement, logger) {
    const log = logger || win.console || console;
    const existing = Object.getOwnPropertyDescriptor(win, name);
    if (existing && !existing.configurable) return false;
    let warned = false;
    function warn() {
        if (warned) return;
        warned = true;
        log.warn('[LEDMatrix] window.' + name + ' is deprecated' +
                 (replacement ? '; use ' + replacement + ' instead' : '') + '.');
    }
    if (typeof target === 'function') {
        const alias = function() {
            warn();
            return target.apply(this, arguments);
        };
        Object.defineProperty(win, name, { value: alias, configurable: true, writable: true });
    } else {
        Object.defineProperty(win, name, {
            get: function() { warn(); return target; },
            configurable: true,
        });
    }
    return true;
}

/**
 * Build the facade object. `win` is the window it reads the classic globals
 * from; `api` and `registry` come from core/api.js and core/registry.js.
 */
export function createFacade(win, api, registry, logger) {
    const log = logger || win.console || console;
    const pages = Object.freeze({
        register: registry.register,
        refresh: registry.refresh,
        list: registry.list,
    });
    const facade = {
        version: FACADE_VERSION,
        api: api,
        pages: pages,
        notify: function(message, type) {
            const notify = win.showNotification;
            if (typeof notify === 'function') return notify(message, type || 'info');
            (type === 'error' ? log.error : log.log).call(log, '[LEDMatrix] ' + message);
            return undefined;
        },
        deprecate: function(name, target, replacement) {
            return defineDeprecatedAlias(win, name, target, replacement, log);
        },
    };
    Object.defineProperty(facade, 'escape', { get: function() { return win.LEDEscape; }, enumerable: true });
    Object.defineProperty(facade, 'widgets', { get: function() { return win.LEDMatrixWidgets; }, enumerable: true });
    return Object.freeze(facade);
}

/** Publish the facade as window.LEDMatrix (replacing an earlier one, if any). */
export function installFacade(win, facade) {
    Object.defineProperty(win, 'LEDMatrix', { value: facade, configurable: true, enumerable: true });
    return facade;
}
