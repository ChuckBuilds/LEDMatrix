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

// Converted pages. Each loads on first use: its module is fetched only when
// its partial (data-page="<name>") first appears.
registry.register('cache', function() { return import('../pages/cache.js'); });

// Old globals the converted pages used to define.
facade.deprecate('deleteCacheFile', function(key) {
    return import('../pages/cache.js').then(function(page) { return page.deleteCacheFile(key); });
}, "the Cache tab's Delete buttons");

registry.start();
