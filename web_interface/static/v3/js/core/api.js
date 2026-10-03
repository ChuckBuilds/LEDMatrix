/*
 * core/api.js -- one fetch wrapper for the interface's own JSON API.
 *
 *   const api = createApi();
 *   const body = await api.get('/api/v3/cache/list', { signal });
 *   await api.post('/api/v3/cache/delete', { key }, { signal });
 *   await api.request('POST', '/api/v3/backup/validate', { body: formData });
 *
 * Every call resolves to the parsed JSON body, or rejects with an ApiError:
 *   error.status         the HTTP status (0 when no HTTP answer arrived)
 *   error.body           the parsed JSON body, when there was one
 *   error.network        true when fetch() itself failed (service restarting)
 *   error.loginRequired  true when the optional web login (#683) wants the
 *                        user to sign in again; the page is already navigating
 *                        to the login form, so callers should show nothing
 * A body of {"status": "error"} is an error even with HTTP 200: several
 * endpoints still answer that way.
 *
 * Login redirect. base.html wraps window.fetch before any other script runs:
 * a 401 carrying X-LEDMatrix-Login sends the browser to that login page. This
 * module calls window.fetch at call time (never a copy taken at import), so
 * every request made here goes through that same wrapper and gets the same
 * redirect. isLoginRedirect() is the wrapper's test, used here only to turn
 * that answer into a quiet `loginRequired` error instead of an error message
 * that would flash up while the page navigates away.
 *
 * An aborted request (ctx.signal from the page registry) rejects with the
 * DOMException named AbortError, untouched, so callers can ignore it.
 */

export class ApiError extends Error {
    constructor(message, details = {}) {
        super(message);
        this.name = 'ApiError';
        this.status = details.status || 0;
        this.body = details.body === undefined ? null : details.body;
        this.network = !!details.network;
        this.loginRequired = !!details.loginRequired;
        if (details.cause !== undefined) this.cause = details.cause;
    }
}

/** True for the optional web login's "sign in again" answer (see base.html). */
export function isLoginRedirect(response) {
    if (!response || response.status !== 401 || !response.headers) return false;
    const login = response.headers.get('X-LEDMatrix-Login');
    return !!login && login.charAt(0) === '/' && login.charAt(1) !== '/';
}

/** True for an AbortError from a cancelled request. */
export function isAbort(error) {
    return !!error && error.name === 'AbortError';
}

// Only this interface's own paths: "/api/...", never "//host" or a full URL.
function checkPath(url) {
    if (typeof url !== 'string' || url.charAt(0) !== '/' || url.charAt(1) === '/' ||
            /[\\\s]/.test(url)) {
        throw new TypeError('LEDMatrix.api: not a path on this server: ' + String(url));
    }
    return url;
}

/**
 * @param {object} [options]
 * @param {Function} [options.fetch]  fetch implementation (tests); default window.fetch at call time
 */
export function createApi(options = {}) {
    const doFetch = options.fetch || function(url, init) { return globalThis.fetch(url, init); };

    async function request(method, url, opts = {}) {
        checkPath(url); // a bug in the caller, not a network failure
        const init = {
            method: method,
            headers: Object.assign({ 'Accept': 'application/json' }, opts.headers || {}),
            signal: opts.signal,
        };
        if (opts.json !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.json);
        } else if (opts.body !== undefined) {
            // Sent as it is (a FormData upload, say); the browser sets the
            // Content-Type, multipart boundary included.
            init.body = opts.body;
        }

        let response;
        try {
            response = await doFetch(url, init);
        } catch (error) {
            if (isAbort(error)) throw error;
            throw new ApiError((error && error.message) || 'Network error',
                               { network: true, cause: error });
        }

        if (isLoginRedirect(response)) {
            throw new ApiError('Signing in again', { status: 401, loginRequired: true });
        }

        let body = null;
        let parseError = null;
        try {
            const text = await response.text();
            body = text ? JSON.parse(text) : null;
        } catch (error) {
            if (isAbort(error)) throw error;
            parseError = error;
        }

        const message = body && typeof body.message === 'string' && body.message
            ? body.message : null;
        if (!response.ok) {
            throw new ApiError(message || ('HTTP ' + response.status),
                               { status: response.status, body: body, cause: parseError || undefined });
        }
        if (parseError || body === null || typeof body !== 'object') {
            throw new ApiError('Unreadable response from the server (HTTP ' + response.status + ')',
                               { status: response.status, cause: parseError || undefined });
        }
        if (body.status === 'error') {
            throw new ApiError(message || 'The request failed', { status: response.status, body: body });
        }
        return body;
    }

    return {
        request: request,
        get: function(url, opts) { return request('GET', url, opts); },
        post: function(url, json, opts) { return request('POST', url, Object.assign({}, opts, { json: json })); },
        put: function(url, json, opts) { return request('PUT', url, Object.assign({}, opts, { json: json })); },
        del: function(url, opts) { return request('DELETE', url, opts); },
    };
}
