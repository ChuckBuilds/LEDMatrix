// LED Matrix v3 JavaScript
// Additional helpers for HTMX and Alpine.js integration

// Global notification system
window.showNotification = function(message, type = 'info') {
    // Use Alpine.js notification if available
    if (window.Alpine) {
        // This would trigger the Alpine.js notification system
        const event = new CustomEvent('show-notification', {
            detail: { message, type }
        });
        document.dispatchEvent(event);
    } else {
        // Fallback notification
        console.log(`${type}: ${message}`);
    }
};

// HTMX response handlers
document.body.addEventListener('htmx:beforeRequest', function(event) {
    // Show loading states for buttons
    const btn = event.target.closest('button, .btn');
    if (btn) {
        btn.classList.add('loading');
        const textEl = btn.querySelector('.btn-text');
        if (textEl) textEl.style.opacity = '0.5';
    }
});

document.body.addEventListener('htmx:afterRequest', function(event) {
    // Remove loading states
    const btn = event.target.closest('button, .btn');
    if (btn) {
        btn.classList.remove('loading');
        const textEl = btn.querySelector('.btn-text');
        if (textEl) textEl.style.opacity = '1';
    }

    // Handle response notifications
    const response = event.detail.xhr;
    if (response && response.responseText) {
        try {
            const data = JSON.parse(response.responseText);
            if (data.message) {
                showNotification(data.message, data.status || 'info');
            }
        } catch (e) {
            // Not JSON, ignore
        }
    }
});

// SSE reconnection helper
window.reconnectSSE = function() {
    if (window.statsSource) {
        window.statsSource.close();
        window.statsSource = new EventSource('/api/v3/stream/stats');
        window.statsSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            updateSystemStats(data);
        };
    }

    if (window.displaySource) {
        window.displaySource.close();
        window.displaySource = new EventSource('/api/v3/stream/display');
        window.displaySource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            // Handle display updates
        };
    }
};

// Utility functions
window.hexToRgb = function(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : null;
};

window.rgbToHex = function(r, g, b) {
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
};

// Form validation helpers
window.validateForm = function(form) {
    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');
    let isValid = true;

    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.classList.add('border-red-500');
            isValid = false;
        } else {
            input.classList.remove('border-red-500');
        }
    });

    return isValid;
};

// Auto-resize textareas
document.addEventListener('DOMContentLoaded', function() {
    const textareas = document.querySelectorAll('textarea');
    textareas.forEach(textarea => {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        });
    });
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + R to refresh
    if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
        e.preventDefault();
        location.reload();
    }

    // Ctrl/Cmd + S to save current form
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        const form = document.querySelector('form');
        if (form) {
            form.dispatchEvent(new Event('submit'));
        }
    }
});

// Plugin management helpers
window.installPlugin = function(pluginId) {
    fetch('/api/v3/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: pluginId })
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            // Refresh plugin list
            htmx.ajax('GET', '/v3/partials/plugins', '#plugins-content');
        }
    })
    .catch(error => {
        showNotification('Error installing plugin: ' + error.message, 'error');
    });
};

// Font management helpers
window.uploadFont = function(fileInput) {
    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('font_file', file);
    formData.append('font_family', file.name.replace(/\.[^/.]+$/, '').toLowerCase().replace(/[^a-z0-9]/g, '_'));

    fetch('/api/v3/fonts/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        showNotification(data.message, data.status);
        if (data.status === 'success') {
            // Refresh fonts list
            htmx.ajax('GET', '/v3/partials/fonts', '#fonts-content');
        }
    })
    .catch(error => {
        showNotification('Error uploading font: ' + error.message, 'error');
    });
};

// Tab switching helper
window.switchTab = function(tabName) {
    // Update Alpine.js active tab if available
    if (window.Alpine) {
        // Dispatch event for Alpine.js
        const event = new CustomEvent('switch-tab', {
            detail: { tab: tabName }
        });
        document.dispatchEvent(event);
    }
};

// Error handling for unhandled promise rejections
window.addEventListener('unhandledrejection', function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    showNotification('An unexpected error occurred', 'error');
});

// Performance monitoring
window.performanceMonitor = {
    startTime: performance.now(),

    mark: function(name) {
        if (window.performance.mark) {
            performance.mark(name);
        }
    },

    measure: function(name, start, end) {
        if (window.performance.measure) {
            performance.measure(name, start, end);
        }
    },

    getMeasures: function() {
        if (window.performance && window.performance.getEntriesByType) {
            return window.performance.getEntriesByType('measure');
        }
        return [];
    },
    
    getMetrics: function() {
        if (!window.performance || !window.performance.getEntriesByType) {
            return {};
        }
        
        const navigation = window.performance.getEntriesByType('navigation')[0];
        const paint = window.performance.getEntriesByType('paint');
        const resources = window.performance.getEntriesByType('resource');
        
        return {
            domContentLoaded: navigation ? navigation.domContentLoadedEventEnd - navigation.domContentLoadedEventStart : 0,
            loadComplete: navigation ? navigation.loadEventEnd - navigation.fetchStart : 0,
            firstPaint: paint.find(p => p.name === 'first-paint')?.startTime || 0,
            firstContentfulPaint: paint.find(p => p.name === 'first-contentful-paint')?.startTime || 0,
            resourceCount: resources.length,
            totalResourceSize: resources.reduce((sum, r) => sum + (r.transferSize || 0), 0),
            measures: this.measures
        };
    },
    
    logMetrics: function() {
        const metrics = this.getMetrics();
        console.group('Performance Metrics');
        console.log('DOM Content Loaded:', metrics.domContentLoaded?.toFixed(2) || 'N/A', 'ms');
        console.log('Load Complete:', metrics.loadComplete?.toFixed(2) || 'N/A', 'ms');
        console.log('First Paint:', metrics.firstPaint?.toFixed(2) || 'N/A', 'ms');
        console.log('First Contentful Paint:', metrics.firstContentfulPaint?.toFixed(2) || 'N/A', 'ms');
        console.log('Resources:', metrics.resourceCount || 0, 'files,', (metrics.totalResourceSize / 1024).toFixed(2) || '0', 'KB');
        if (Object.keys(metrics.measures || {}).length > 0) {
            console.log('Custom Measures:', metrics.measures);
        }
        console.groupEnd();
    }
};

// Initialize performance monitoring
document.addEventListener('DOMContentLoaded', function() {
    window.performanceMonitor.mark('app-start');
    
    // Log metrics after page load
    window.addEventListener('load', function() {
        setTimeout(() => {
            window.performanceMonitor.mark('app-loaded');
            window.performanceMonitor.measure('app-load-time', 'app-start', 'app-loaded');
            if (window.location.search.includes('debug=perf')) {
                window.performanceMonitor.logMetrics();
            }
        }, 100);
    });
});
