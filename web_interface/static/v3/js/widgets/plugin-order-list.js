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
 *       showVegasModeBadge: true                  // optional: Scroll/Fixed/Static badge
 *   });
 *
 * The container re-renders from /api/v3/plugins/installed each init; the
 * hidden input(s) must already hold the saved order/exclusions (JSON).
 */
(function() {
    'use strict';

    const MODE_LABELS = new Map([
        ['scroll', { label: 'Scroll', icon: 'fa-scroll', color: 'text-blue-600' }],
        ['fixed',  { label: 'Fixed',  icon: 'fa-square', color: 'text-green-600' }],
        ['static', { label: 'Static', icon: 'fa-pause',  color: 'text-orange-600' }]
    ]);

    function init(options) {
        const container = document.getElementById(options.containerId);
        const orderInput = document.getElementById(options.orderInputId);
        const excludedInput = options.excludedInputId ? document.getElementById(options.excludedInputId) : null;
        if (!container || !orderInput) return;

        function syncInputs() {
            const order = [];
            const excluded = [];
            container.querySelectorAll('.plugin-order-item').forEach(item => {
                const pluginId = item.dataset.pluginId;
                order.push(pluginId);
                const checkbox = item.querySelector('.plugin-order-include');
                if (checkbox && !checkbox.checked) excluded.push(pluginId);
            });
            orderInput.value = JSON.stringify(order);
            if (excludedInput) excludedInput.value = JSON.stringify(excluded);
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

        fetch('/api/v3/plugins/installed')
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
                        const vegasMode = plugin.vegas_mode || plugin.vegas_content_type || 'fixed';
                        const modeInfo = MODE_LABELS.get(vegasMode) || MODE_LABELS.get('fixed');
                        const badge = document.createElement('span');
                        badge.className = `text-xs ${modeInfo.color} ml-2`;
                        badge.title = `Vegas display mode: ${modeInfo.label}`;
                        const badgeIcon = document.createElement('i');
                        badgeIcon.className = `fas ${modeInfo.icon} mr-1`;
                        badge.appendChild(badgeIcon);
                        badge.appendChild(document.createTextNode(modeInfo.label));
                        row.appendChild(badge);
                    }

                    container.appendChild(row);
                });

                setupDragAndDrop();
                container.querySelectorAll('.plugin-order-include').forEach(checkbox => {
                    checkbox.addEventListener('change', syncInputs);
                });
                syncInputs();
            })
            .catch(error => {
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
