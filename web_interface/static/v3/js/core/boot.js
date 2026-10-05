/*
 * core/boot.js -- the entry module. base.html loads it with
 * <script type="module">; everything else under js/core/ and js/pages/ is
 * reached through imports from here. No bundler: the Pi serves these files
 * as they are (see docs/WEB_FRONTEND_ARCHITECTURE.md).
 *
 * Modules run deferred, after the HTML is parsed, so a tab partial may have
 * been swapped in before this file runs. registry.start() mounts any page
 * already on the screen, so the order does not matter.
 */
import { createApi } from './api.js';
import { createFacade, installFacade } from './facade.js';
import { createRegistry } from './registry.js';

const api = createApi();
const registry = createRegistry({
    context: {
        api: api,
        notify: function(message, type) { return window.LEDMatrix.notify(message, type); },
    },
});
const facade = installFacade(window, createFacade(window, api, registry));

/**
 * A page's loader. It remembers the module once loaded, so an alias of a
 * synchronous old global (validateJSON returns a boolean) can answer
 * synchronously while its page is on screen.
 */
function page(load) {
    let module = null;
    const loader = function() {
        return Promise.resolve(load()).then(function(loaded) { module = loaded; return loaded; });
    };
    loader.loaded = function() { return module; };
    return loader;
}

// Converted pages. Each loads on first use: its module is fetched only when
// its partial (data-page="<name>") first appears.
const pages = {
    'cache': page(function() { return import('../pages/cache.js'); }),
    'durations': page(function() { return import('../pages/durations.js'); }),
    'operation-history': page(function() { return import('../pages/operation-history.js'); }),
    'raw-json': page(function() { return import('../pages/raw-json.js'); }),
    'backup-restore': page(function() { return import('../pages/backup-restore.js'); }),
    'schedule': page(function() { return import('../pages/schedule.js'); }),
    'general': page(function() { return import('../pages/general.js'); }),
};
Object.keys(pages).forEach(function(name) { registry.register(name, pages[name]); });

/** A function that calls `pick(module)` with its arguments, loading the page's module if needed. */
function forward(pageName, pick) {
    const loader = pages[pageName];
    return function() {
        const args = arguments;
        const module = loader.loaded();
        if (module) return pick(module).apply(null, args);
        return loader().then(function(loaded) { return pick(loaded).apply(null, args); });
    };
}

/** Keep window[name] working: forward to the page module's export of the same name. */
function alias(pageName, name, replacement) {
    facade.deprecate(name, forward(pageName, function(module) { return module[name]; }), replacement);
}

/** The same for an old global object of functions: each method forwards to the export's method. */
function aliasObject(pageName, name, methods, replacement) {
    const target = Object.fromEntries(methods.map(function(method) {
        return [method, forward(pageName, function(module) { return module[name][method]; })];
    }));
    facade.deprecate(name, Object.freeze(target), replacement);
}

// Old globals the converted pages used to define.
alias('cache', 'deleteCacheFile', "the Cache tab's Delete buttons");
['formatJson', 'manualValidateJson', 'validateJSON', 'saveMainConfig', 'saveSecretsConfig'].forEach(function(name) {
    alias('raw-json', name, "the Config Editor tab's buttons");
});
['exportBackup', 'loadBackupList', 'validateRestoreFile', 'clearRestore', 'runRestore'].forEach(function(name) {
    alias('backup-restore', name, "the Backup & Restore tab's buttons");
});
['handleScheduleResponse', 'handleDimScheduleResponse'].forEach(function(name) {
    alias('schedule', name, "the Schedule tab's own save handling");
});
aliasObject('general', 'webLogin', ['setPassword', 'disable', 'createToken', 'copyToken', 'revoke'],
            "the General tab's Security buttons");

registry.start();
