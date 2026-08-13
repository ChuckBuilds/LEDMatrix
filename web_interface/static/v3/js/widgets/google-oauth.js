/**
 * Google OAuth Widget
 *
 * Step 2 of the calendar plugin's setup, between uploading the OAuth client
 * file and picking calendars. Google will not let a headless device complete
 * consent on its own, so the flow is necessarily two calls with a human in
 * between:
 *
 *   1. POST /api/v3/plugins/calendar/authenticate with no body
 *      -> { auth_url } to open in a browser
 *   2. the browser lands on a loopback address that fails to load; its URL
 *      carries the authorization code. POST it back as redirect_url
 *      -> the server exchanges it and writes token.pickle
 *
 * The failed page in step 2 is expected and is worth saying out loud, because
 * it looks exactly like something went wrong.
 *
 * @module GoogleOAuthWidget
 */

(function () {
    'use strict';

    if (typeof window.LEDMatrixWidgets === 'undefined') {
        console.error('[GoogleOAuthWidget] LEDMatrixWidgets registry not found. Load registry.js first.');
        return;
    }

    const ENDPOINT = '/api/v3/plugins/calendar/authenticate';

    window.LEDMatrixWidgets.register('google-oauth', {
        name: 'Google OAuth Widget',
        version: '1.0.0',

        /**
         * @param {HTMLElement} container
         * @param {Object} config  - schema config (unused)
         * @param {*}      value   - unused; this widget stores nothing
         * @param {Object} options - { fieldId, pluginId, name }
         */
        render: function (container, config, value, options) {
            const fieldId = options.fieldId;

            // Nothing is stored in config by this step -- the result is
            // token.pickle on the device -- but the form still expects a field.
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.id = fieldId + '_hidden';
            hidden.name = options.name;
            hidden.value = value || '';

            const startBtn = document.createElement('button');
            startBtn.type = 'button';
            startBtn.className = 'px-3 py-1.5 text-sm rounded-md bg-blue-600 hover:bg-blue-700 text-white';
            startBtn.innerHTML = '<i class="fas fa-key"></i> Connect Google Account';

            const status = document.createElement('p');
            status.className = 'text-xs text-gray-400 mt-2';

            const step2 = document.createElement('div');
            step2.className = 'mt-3 hidden';

            const link = document.createElement('a');
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.className = 'text-blue-400 underline text-sm break-all';
            link.textContent = 'Open the Google consent screen';

            const hint = document.createElement('p');
            hint.className = 'text-xs text-gray-400 mt-2';
            hint.textContent =
                'After approving, your browser will try to open a page that fails '
                + 'to load. That is expected. Copy its full address from the bar '
                + 'and paste it below.';

            const codeInput = document.createElement('input');
            codeInput.type = 'text';
            codeInput.placeholder = 'http://127.0.0.1/?code=...';
            codeInput.className =
                'mt-2 block w-full px-3 py-2 text-sm border border-gray-600 '
                + 'rounded-md bg-gray-800 text-gray-100';

            const finishBtn = document.createElement('button');
            finishBtn.type = 'button';
            finishBtn.className = 'mt-2 px-3 py-1.5 text-sm rounded-md bg-green-600 hover:bg-green-700 text-white';
            finishBtn.innerHTML = '<i class="fas fa-check"></i> Finish Authentication';

            function say(message, kind) {
                status.textContent = message;
                status.className = 'text-xs mt-2 ' + (
                    kind === 'error' ? 'text-red-400'
                        : kind === 'success' ? 'text-green-400'
                            : 'text-gray-400');
            }

            function post(body) {
                return fetch(ENDPOINT, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body || {})
                }).then(function (r) {
                    return r.json().catch(function () {
                        // A non-JSON body here means the request never reached
                        // the handler -- worth saying so rather than "undefined".
                        return { status: 'error', message: 'Server returned ' + r.status };
                    });
                });
            }

            startBtn.addEventListener('click', function () {
                startBtn.disabled = true;
                say('Requesting a consent link...');
                post({}).then(function (data) {
                    startBtn.disabled = false;
                    if (data.status !== 'success' || !data.auth_url) {
                        say(data.message || 'Could not start authentication.', 'error');
                        return;
                    }
                    link.href = data.auth_url;
                    step2.classList.remove('hidden');
                    say(data.message || 'Open the link, approve, then paste the address back.');
                }).catch(function (err) {
                    startBtn.disabled = false;
                    say('Request failed: ' + err.message, 'error');
                });
            });

            finishBtn.addEventListener('click', function () {
                const pasted = codeInput.value.trim();
                if (!pasted) {
                    say('Paste the address your browser was redirected to.', 'error');
                    return;
                }
                finishBtn.disabled = true;
                say('Exchanging the code with Google...');
                post({ redirect_url: pasted }).then(function (data) {
                    finishBtn.disabled = false;
                    if (data.status !== 'success') {
                        say(data.message || 'Authentication failed.', 'error');
                        return;
                    }
                    say(data.message || 'Authenticated.', 'success');
                    step2.classList.add('hidden');
                    codeInput.value = '';
                }).catch(function (err) {
                    finishBtn.disabled = false;
                    say('Request failed: ' + err.message, 'error');
                });
            });

            step2.appendChild(link);
            step2.appendChild(hint);
            step2.appendChild(codeInput);
            step2.appendChild(finishBtn);

            container.appendChild(hidden);
            container.appendChild(startBtn);
            container.appendChild(status);
            container.appendChild(step2);
        },

        getValue: function (fieldId) {
            const hidden = document.getElementById(fieldId + '_hidden');
            return hidden ? hidden.value : '';
        }
    });
})();
