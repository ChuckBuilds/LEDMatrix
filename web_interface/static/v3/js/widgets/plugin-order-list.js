/**
 * Plugin Order List — shared drag-and-drop reorder list of enabled plugins.
 *
 * Factored out of the Vegas Scroll section of display.html so both Vegas mode
 * and the primary rotation (Durations tab) use one implementation. Renders
 * one draggable row per enabled plugin into a container and keeps a hidden
 * input's value in sync as a JSON array of plugin ids in display order.
 *
 * Usage:
 *   PluginOrderList.init({
 *       containerId: 'vegas_plugin_order',        // rows render here
 *       orderInputId: 'vegas_plugin_order_value', // hidden input, JSON array of ids
 *       excludedInputId: 'vegas_excluded_plugins_value', // optional: adds an
 *           // include-checkbox per row; unchecked ids collect here (JSON array)
 *       showVegasModeBadge: true,                 // optional: Scroll/Pause/Excluded badge
 *       signal: ctx.signal                        // optional: AbortSignal that cancels
 *           // the plugin-list request (a page module's ctx.signal)
 *   });
 *
 * The container re-renders from /api/v3/plugins/installed each init; the
 * hidden input(s) must already hold the saved order/exclusions (JSON). Saved
 * ids without a row (disabled plugins) stay in them, in their saved places.
 */
(function() {
    'use strict';

    // Keyed by Vegas participation ('scroll' | 'pause' | 'exclude'); 'fixed'
    // and 'static' are the legacy vegas_mode values, for an older API.
    const MODE_LABELS = new Map([
        ['scroll',  { label: 'Scroll',   icon: 'fa-scroll', color: 'text-blue-600' }],
        ['pause',   { label: 'Pause',    icon: 'fa-pause',  color: 'text-orange-600' }],
        ['exclude', { label: 'Excluded', icon: 'fa-ban',    color: 'text-gray-500' }],
        ['fixed',   { label: 'Scroll',   icon: 'fa-scroll', color: 'text-blue-600' }],
        ['static',  { label: 'Pause',    icon: 'fa-pause',  color: 'text-orange-600' }]
    ]);

    function init(options) {
        const container = document.getElementById(options.containerId);
        const orderInput = document.getElementById(options.orderInputId);
        const excludedInput = options.excludedInputId ? document.getElementById(options.excludedInputId) : null;
        if (!container || !orderInput) return;

        // The saved lists as the inputs held them when the rows were drawn.
        // Only enabled plugins get a row, and the inputs are rewritten from
        // the rows, so a disabled plugin's place and exclusion have to be
        // carried over from these: dropped, the next Display or Durations
        // save stored the lists without it, and once re-enabled it came back
        // at the end of the rotation and scrolling in Vegas again.
        let savedOrder = [];
        let savedExcluded = [];

        // Saved ids with no row, once each. Only strings: /config/main
        // refuses a list holding anything else, which would block every save.
        function unlisted(saved, rowIds) {
            const seen = new Set(rowIds);
            return saved.filter(id => {
                if (typeof id !== 'string' || seen.has(id)) return false;
                seen.add(id);
                return true;
            });
        }

        function syncInputs() {
            const rowIds = [];
            const excluded = [];
            container.querySelectorAll('.plugin-order-item').forEach(item => {
                const pluginId = item.dataset.pluginId;
                rowIds.push(pluginId);
                const checkbox = item.querySelector('.plugin-order-include');
                if (checkbox && !checkbox.checked) excluded.push(pluginId);
            });
            // An id without a row keeps its saved slot; the rows fill the
            // other slots in their current order, and any rows left over
            // (plugins not in the saved order) go last.
            const kept = new Set(unlisted(savedOrder, rowIds));
            const order = [];
            let next = 0;
            savedOrder.forEach(id => {
                if (kept.has(id)) {
                    order.push(id);
                    kept.delete(id);
                } else if (rowIds.includes(id) && next < rowIds.length) {
                    order.push(rowIds[next++]);
                }
            });
            orderInput.value = JSON.stringify(order.concat(rowIds.slice(next)));
            if (excludedInput) {
                excludedInput.value = JSON.stringify(excluded.concat(unlisted(savedExcluded, rowIds)));
            }
        }

        function setupDragAndDrop() {
            let draggedItem = null;
            container.querySelectorAll('.plugin-order-item').forEach(item => {
                item.addEventListener('dragstart', function(e) {
                    draggedItem = this;
                    this.style.opacity = '0.5';
                    e.dataTransfer.effectAllowed = 'move';
                });
                item.addEventListener('dragend', function() {
                    this.style.opacity = '1';
                    draggedItem = null;
                    syncInputs();
                });
                item.addEventListener('dragover', function(e) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    const rect = this.getBoundingClientRect();
                    const midY = rect.top + rect.height / 2;
                    if (e.clientY < midY) {
                        this.style.borderTop = '2px solid #3b82f6';
                        this.style.borderBottom = '';
                    } else {
                        this.style.borderBottom = '2px solid #3b82f6';
                        this.style.borderTop = '';
                    }
                });
                item.addEventListener('dragleave', function() {
                    this.style.borderTop = '';
                    this.style.borderBottom = '';
                });
                item.addEventListener('drop', function(e) {
                    e.preventDefault();
                    this.style.borderTop = '';
                    this.style.borderBottom = '';
                    if (draggedItem && draggedItem !== this) {
                        const rect = this.getBoundingClientRect();
                        const midY = rect.top + rect.height / 2;
                        if (e.clientY < midY) {
                            container.insertBefore(draggedItem, this);
                        } else {
                            container.insertBefore(draggedItem, this.nextSibling);
                        }
                    }
                });
            });
        }

        fetch('/api/v3/plugins/installed', { signal: options.signal })
            .then(response => response.json())
            .then(data => {
                const allPlugins = (data.data && data.data.plugins) || data.plugins || [];
                const plugins = allPlugins.filter(p => p.enabled);
                if (plugins.length === 0) {
                    const empty = document.createElement('p');
                    empty.className = 'text-sm text-gray-500 italic';
                    empty.textContent = 'No enabled plugins';
                    container.textContent = '';
                    container.appendChild(empty);
                    return;
                }

                let currentOrder = [];
                let excluded = [];
                try {
                    currentOrder = JSON.parse(orderInput.value || '[]');
                    if (excludedInput) excluded = JSON.parse(excludedInput.value || '[]');
                } catch (e) {
                    console.error('Error parsing saved plugin order:', e);
                }
                // JSON.parse can succeed and still return null/objects
                // (e.g. a saved value of "null"); normalize to arrays.
                if (!Array.isArray(currentOrder)) currentOrder = [];
                if (!Array.isArray(excluded)) excluded = [];
                savedOrder = currentOrder;
                savedExcluded = excluded;

                // Saved order first, then any newly enabled plugins.
                const orderedPlugins = [];
                currentOrder.forEach(id => {
                    const plugin = plugins.find(p => p.id === id);
                    if (plugin) orderedPlugins.push(plugin);
                });
                plugins.forEach(plugin => {
                    if (!orderedPlugins.find(p => p.id === plugin.id)) orderedPlugins.push(plugin);
                });

                // Rows are built with DOM APIs rather than innerHTML — plugin
                // ids/names come from installed manifests (semi-trusted).
                container.textContent = '';
                orderedPlugins.forEach(plugin => {
                    const row = document.createElement('div');
                    row.className = 'flex items-center p-2 bg-gray-50 rounded border border-gray-200 cursor-move plugin-order-item';
                    row.dataset.pluginId = plugin.id;
                    row.draggable = true;

                    const grip = document.createElement('i');
                    grip.className = 'fas fa-grip-vertical text-gray-400 mr-3';
                    row.appendChild(grip);

                    if (excludedInput) {
                        const isExcluded = excluded.includes(plugin.id);
                        const label = document.createElement('label');
                        label.className = 'flex items-center flex-1';
                        const checkbox = document.createElement('input');
                        checkbox.type = 'checkbox';
                        checkbox.className = 'plugin-order-include h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded mr-2';
                        checkbox.checked = !isExcluded;
                        const name = document.createElement('span');
                        name.className = 'text-sm font-medium text-gray-700';
                        name.textContent = plugin.name || plugin.id;
                        label.appendChild(checkbox);
                        label.appendChild(name);
                        row.appendChild(label);
                    } else {
                        const name = document.createElement('span');
                        name.className = 'text-sm font-medium text-gray-700 flex-1';
                        name.textContent = plugin.name || plugin.id;
                        row.appendChild(name);
                    }

                    if (options.showVegasModeBadge) {
                        const vegasMode = plugin.vegas_participation || plugin.vegas_mode || 'scroll';
                        const modeInfo = MODE_LABELS.get(vegasMode) || MODE_LABELS.get('scroll');
                        const badge = document.createElement('span');
                        badge.className = `text-xs ${modeInfo.color} ml-2`;
                        badge.title = `Vegas participation: ${modeInfo.label}`;
                        const badgeIcon = document.createElement('i');
                        badgeIcon.className = `fas ${modeInfo.icon} mr-1`;
                        badge.appendChild(badgeIcon);
                        badge.appendChild(document.createTextNode(modeInfo.label));
                        row.appendChild(badge);
                    }

                    // Up/down buttons: touch- and keyboard-accessible
                    // reordering alongside native drag-and-drop (HTML5 drag
                    // events don't fire on most mobile browsers).
                    const pluginLabel = plugin.name || plugin.id;
                    [['up', 'fa-chevron-up', `Move ${pluginLabel} up`],
                     ['down', 'fa-chevron-down', `Move ${pluginLabel} down`]].forEach(([dir, iconCls, ariaLabel]) => {
                        const moveBtn = document.createElement('button');
                        moveBtn.type = 'button';
                        moveBtn.className = 'plugin-order-move text-gray-400 hover:text-gray-700 px-2 py-1';
                        moveBtn.setAttribute('aria-label', ariaLabel);
                        const moveIcon = document.createElement('i');
                        moveIcon.className = `fas ${iconCls} text-xs`;
                        moveBtn.appendChild(moveIcon);
                        moveBtn.addEventListener('click', function() {
                            if (dir === 'up' && row.previousElementSibling) {
                                container.insertBefore(row, row.previousElementSibling);
                            } else if (dir === 'down' && row.nextElementSibling) {
                                container.insertBefore(row.nextElementSibling, row);
                            }
                            syncInputs();
                            moveBtn.focus();
                        });
                        row.appendChild(moveBtn);
                    });

                    container.appendChild(row);
                });

                setupDragAndDrop();
                container.querySelectorAll('.plugin-order-include').forEach(checkbox => {
                    checkbox.addEventListener('change', syncInputs);
                });
                syncInputs();
            })
            .catch(error => {
                // The page was swapped away (options.signal): nothing to draw.
                if (error && error.name === 'AbortError') return;
                console.error('Error fetching plugins:', error);
                const err = document.createElement('p');
                err.className = 'text-sm text-red-500';
                err.textContent = 'Error loading plugins';
                container.textContent = '';
                container.appendChild(err);
            });
    }

    window.PluginOrderList = { init: init };
})();
