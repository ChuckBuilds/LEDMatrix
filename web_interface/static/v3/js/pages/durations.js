/*
 * pages/durations.js -- the Rotation & Durations tab
 * (templates/v3/partials/durations.html).
 *
 * The form itself is plain htmx (hx-post to /api/v3/config/main), so the only
 * code here starts the shared drag-and-drop rotation-order list
 * (static/v3/js/widgets/plugin-order-list.js, the same widget the Vegas
 * section of the Display tab uses). Its plugin-list request carries
 * ctx.signal, so a swap cancels it.
 *
 * The widget is a classic deferred script and normally loads before this
 * module runs; the short retry covers a page that arrives first anyway.
 */

const CONTAINER_ID = 'rotation_plugin_order';
const ORDER_INPUT_ID = 'rotation_plugin_order_value';
const RETRY_MS = 100;
const MAX_TRIES = 50;

export function init(root, ctx) {
    const container = root.querySelector('#' + CONTAINER_ID);
    if (!container) return;
    const win = root.ownerDocument.defaultView;
    let tries = 0;

    function start() {
        ctx.state.timer = null;
        if (ctx.signal.aborted) return;
        const widget = win.PluginOrderList;
        if (!widget) {
            if (++tries < MAX_TRIES) {
                ctx.state.timer = win.setTimeout(start, RETRY_MS);
            } else {
                // Say so rather than showing "Loading..." for ever.
                container.textContent = 'Could not load the reorder widget — reload the page to try again.';
                container.className = 'text-sm text-red-500';
            }
            return;
        }
        widget.init({ containerId: CONTAINER_ID, orderInputId: ORDER_INPUT_ID, signal: ctx.signal });
    }
    start();
}

export function destroy(root, ctx) {
    if (ctx.state.timer) {
        root.ownerDocument.defaultView.clearTimeout(ctx.state.timer);
        ctx.state.timer = null;
    }
}
