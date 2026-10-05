/*
 * pages/schedule.js -- the Schedule tab (templates/v3/partials/schedule.html):
 * when the display is on, and when it dims.
 *
 * Both forms stay plain htmx (hx-post with json-enc). This module:
 *   - draws the two schedule-picker widgets from the saved config the
 *     partial carries in data-schedule-config / data-dim-schedule-config
 *   - reports each form's save in one notification. The forms used to call
 *     window.handleScheduleResponse / handleDimScheduleResponse from an
 *     hx-on attribute; now one htmx:afterRequest listener on the page root
 *     does it, and the forms carry data-reports-result so app.js does not
 *     show the server's message a second time
 *   - keeps the "30%" label next to the dim brightness slider current
 *
 * The widget registry (LEDMatrixWidgets) and the schedule-picker widget are
 * classic deferred scripts and normally load before this module runs; the
 * short retry covers a page that arrives first anyway.
 *
 * The old globals are deprecated aliases made in core/boot.js; they forward
 * to the exports of the same name at the bottom of this file.
 */

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
const RETRY_MS = 100;
const MAX_TRIES = 50;

const PICKERS = [
    {
        label: 'Schedule', container: 'schedule_picker_container', data: 'scheduleConfig',
        fieldId: 'schedule', start: '07:00', end: '23:00',
        // The display schedule fills in days only when it has no "days" at
        // all; the dim schedule also when "days" is empty.
        fillEmptyDays: false,
    },
    {
        label: 'DimSchedule', container: 'dim_schedule_picker_container', data: 'dimScheduleConfig',
        fieldId: 'dim_schedule', start: '20:00', end: '07:00',
        fillEmptyDays: true,
    },
];

const FORMS = {
    schedule_form: { saved: 'Schedule settings saved', failed: 'Error saving schedule' },
    dim_schedule_form: { saved: 'Dim schedule settings saved', failed: 'Error saving dim schedule' },
};

const WIDGET_OPTIONS = { showModeToggle: true, showEnableToggle: true, compactMode: false };

// The mounted page, for the deprecated aliases (boot.js).
let active = null;

function readConfig(root, key) {
    try {
        const parsed = JSON.parse(root.dataset[key] || 'null');
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
        return {};
    }
}

/** The saved config, in the shape the schedule-picker widget takes. */
export function widgetValue(config, spec) {
    config = config || {};
    let mode = 'global';
    if (config.mode) {
        // Normalize mode value (handle both 'per_day' and 'per-day')
        mode = config.mode.replace('-', '_');
    } else if (config.days) {
        mode = 'per_day';
    }
    const value = {
        enabled: config.enabled || false,
        mode: mode,
        start_time: config.start_time || spec.start,
        end_time: config.end_time || spec.end,
        days: config.days || {},
    };
    const noDays = !config.days || (spec.fillEmptyDays && Object.keys(config.days).length === 0);
    if (noDays) {
        value.days = {};
        DAYS.forEach(function(day) {
            value.days[day] = { enabled: true, start_time: spec.start, end_time: spec.end };
        });
    }
    return value;
}

/** One notification for a schedule form's answer. */
function report(notify, xhr, labels) {
    let response;
    try {
        response = JSON.parse(xhr.responseText);
    } catch (e) {
        response = { status: 'error', message: 'Invalid response from server' };
    }
    if (!response || typeof response !== 'object') {
        response = { status: 'error', message: 'Invalid response from server' };
    }
    const message = response.message || (response.status === 'success' ? labels.saved : labels.failed);
    notify(message, response.status || 'info');
}

function drawPickers(root, ctx) {
    const win = root.ownerDocument.defaultView;
    let tries = 0;
    function attempt() {
        ctx.state.timer = null;
        if (ctx.signal.aborted) return;
        const registry = win.LEDMatrixWidgets;
        const widget = registry && registry.get('schedule-picker');
        if (!widget) {
            if (++tries < MAX_TRIES) {
                ctx.state.timer = win.setTimeout(attempt, RETRY_MS);
            } else {
                win.console.error(registry
                    ? '[Schedule] schedule-picker widget not registered'
                    : '[Schedule] LEDMatrixWidgets registry not available');
            }
            return;
        }
        PICKERS.forEach(function(spec) {
            const container = root.querySelector('#' + spec.container);
            if (!container) {
                win.console.error('[' + spec.label + '] Container not found');
                return;
            }
            widget.render(container, { 'x-options': WIDGET_OPTIONS },
                          widgetValue(readConfig(root, spec.data), spec), { fieldId: spec.fieldId });
        });
    }
    attempt();
}

export function init(root, ctx) {
    const on = { signal: ctx.signal };

    root.addEventListener('htmx:afterRequest', function(event) {
        const form = event.target && event.target.closest ? event.target.closest('form') : null;
        const labels = form && root.contains(form) ? FORMS[form.id] : null;
        if (labels && event.detail && event.detail.xhr) report(ctx.notify, event.detail.xhr, labels);
    }, on);

    const slider = root.querySelector('#dim_brightness');
    const label = root.querySelector('#dim_brightness_display');
    if (slider && label) {
        slider.addEventListener('input', function() { label.textContent = slider.value + '%'; }, on);
    }

    drawPickers(root, ctx);
    active = ctx;
}

export function destroy(root, ctx) {
    if (ctx.state.timer) {
        root.ownerDocument.defaultView.clearTimeout(ctx.state.timer);
        ctx.state.timer = null;
    }
    if (active === ctx) active = null;
}

// ── the old globals, kept as deprecated aliases (boot.js) ────────────────────
function notifier(event) {
    if (active) return active.notify;
    const win = event && event.target && event.target.ownerDocument
        ? event.target.ownerDocument.defaultView : globalThis;
    return function(message, type) { return win.showNotification(message, type); };
}

/** window.handleScheduleResponse(event): an htmx:afterRequest event. */
export function handleScheduleResponse(event) {
    report(notifier(event), event.detail.xhr, FORMS.schedule_form);
}

/** window.handleDimScheduleResponse(event): an htmx:afterRequest event. */
export function handleDimScheduleResponse(event) {
    report(notifier(event), event.detail.xhr, FORMS.dim_schedule_form);
}
