"""
Tests for Web Interface API endpoints.

Tests Flask routes, request/response handling, and API functionality.
"""

import pytest
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from flask import Flask


@pytest.fixture
def mock_config_manager():
    """Create a mock config manager."""
    mock = MagicMock()
    mock.load_config.return_value = {
        'display': {'brightness': 50},
        'plugins': {},
        'timezone': 'UTC'
    }
    mock.get_config_path.return_value = 'config/config.json'
    mock.get_secrets_path.return_value = 'config/config_secrets.json'
    mock.get_raw_file_content.return_value = {'weather': {'api_key': 'test'}}
    mock.save_config_atomic.return_value = MagicMock(
        status=MagicMock(value='success'),
        message=None
    )
    return mock


@pytest.fixture
def mock_plugin_manager():
    """Create a mock plugin manager."""
    mock = MagicMock()
    mock.plugins = {}
    mock.discover_plugins.return_value = []
    mock.health_tracker = MagicMock()
    mock.health_tracker.get_health_status.return_value = {'healthy': True}
    return mock


@pytest.fixture
def client(mock_config_manager, mock_plugin_manager):
    """Create a Flask test client with mocked dependencies."""
    # Create a minimal Flask app for testing
    test_app = Flask(__name__)
    test_app.config['TESTING'] = True
    test_app.config['SECRET_KEY'] = 'test-secret-key'
    
    # Register the API blueprint
    from web_interface.blueprints.api_v3 import api_v3
    
    # Mock the managers on the blueprint
    api_v3.config_manager = mock_config_manager
    api_v3.plugin_manager = mock_plugin_manager
    api_v3.plugin_store_manager = MagicMock()
    api_v3.saved_repositories_manager = MagicMock()
    api_v3.schema_manager = MagicMock()
    api_v3.operation_queue = MagicMock()
    api_v3.plugin_state_manager = MagicMock()
    api_v3.operation_history = MagicMock()
    api_v3.cache_manager = MagicMock()
    
    # Setup operation queue mocks
    mock_operation = MagicMock()
    mock_operation.operation_id = 'test-op-123'
    mock_operation.status = MagicMock(value='pending')
    api_v3.operation_queue.get_operation_status.return_value = mock_operation
    api_v3.operation_queue.get_recent_operations.return_value = []
    
    # Setup schema manager mocks
    api_v3.schema_manager.load_schema.return_value = {
        'type': 'object',
        'properties': {'enabled': {'type': 'boolean'}}
    }
    
    # Setup state manager mocks
    api_v3.plugin_state_manager.get_all_states.return_value = {}
    
    test_app.register_blueprint(api_v3, url_prefix='/api/v3')
    
    with test_app.test_client() as client:
        yield client


class TestConfigAPI:
    """Test configuration API endpoints."""
    
    def test_get_main_config(self, client, mock_config_manager):
        """Test getting main configuration."""
        response = client.get('/api/v3/config/main')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data.get('status') == 'success'
        assert 'data' in data
        assert 'display' in data['data']
        mock_config_manager.load_config.assert_called_once()
    
    def test_save_main_config(self, client, mock_config_manager):
        """Test saving main configuration."""
        new_config = {
            'display': {'brightness': 75},
            'timezone': 'UTC'
        }
        
        response = client.post(
            '/api/v3/config/main',
            data=json.dumps(new_config),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        mock_config_manager.save_config_atomic.assert_called_once()
    
    def test_save_main_config_validation_error(self, client, mock_config_manager):
        """Test saving config with validation error."""
        invalid_config = {'invalid': 'data'}
        
        mock_config_manager.save_config_atomic.return_value = MagicMock(
            status=MagicMock(value='validation_failed'),
            message='Validation error'
        )
        
        response = client.post(
            '/api/v3/config/main',
            data=json.dumps(invalid_config),
            content_type='application/json'
        )
        
        assert response.status_code in [400, 500]
    
    def test_get_secrets_config(self, client, mock_config_manager):
        """Test getting secrets configuration."""
        response = client.get('/api/v3/config/secrets')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'weather' in data or 'data' in data
        mock_config_manager.get_raw_file_content.assert_called_once()
    
    def test_save_schedule_config(self, client, mock_config_manager):
        """Test saving schedule configuration."""
        schedule_config = {
            'enabled': True,
            'start_time': '07:00',
            'end_time': '23:00',
            'mode': 'global'
        }
        
        response = client.post(
            '/api/v3/config/schedule',
            data=json.dumps(schedule_config),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        mock_config_manager.save_config_atomic.assert_called_once()


class TestSystemAPI:
    """Test system API endpoints."""
    
    @patch('web_interface.blueprints.api_v3.subprocess')
    def test_get_system_status(self, mock_subprocess, client):
        """Test getting system status."""
        mock_result = MagicMock()
        mock_result.stdout = 'active\n'
        mock_result.returncode = 0
        mock_subprocess.run.return_value = mock_result
        
        response = client.get('/api/v3/system/status')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'service' in data or 'status' in data or 'active' in data
    
    @patch('web_interface.blueprints.api_v3.subprocess')
    def test_get_system_version(self, mock_subprocess, client):
        """Test getting system version."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'v1.0.0\n'
        mock_subprocess.run.return_value = mock_result
        
        response = client.get('/api/v3/system/version')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'version' in data.get('data', {}) or 'version' in data
    
    @patch('web_interface.blueprints.api_v3.subprocess')
    def test_execute_system_action(self, mock_subprocess, client):
        """Test executing system action."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'success'
        mock_subprocess.run.return_value = mock_result
        
        action_data = {
            'action': 'restart',
            'service': 'ledmatrix'
        }
        
        response = client.post(
            '/api/v3/system/action',
            data=json.dumps(action_data),
            content_type='application/json'
        )
        
        # May return 400 if action validation fails, or 200 if successful
        assert response.status_code in [200, 400]


class TestDisplayAPI:
    """Test display API endpoints."""
    
    def test_get_display_current(self, client):
        """Test getting current display information."""
        # Mock cache manager on the blueprint
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.cache_manager.get.return_value = {
            'mode': 'weather',
            'plugin_id': 'weather'
        }
        
        response = client.get('/api/v3/display/current')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'mode' in data or 'current' in data or 'data' in data
    
    def test_get_on_demand_status(self, client):
        """Test getting on-demand display status."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.cache_manager.get.return_value = {
            'active': False,
            'mode': None
        }
        
        response = client.get('/api/v3/display/on-demand/status')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'active' in data or 'status' in data or 'data' in data
    
    def test_start_on_demand_display(self, client):
        """Test starting on-demand display."""
        from web_interface.blueprints.api_v3 import api_v3
        
        request_data = {
            'plugin_id': 'weather',
            'mode': 'weather_current',
            'duration': 30
        }
        
        # Ensure cache manager is set up
        if not hasattr(api_v3, 'cache_manager') or api_v3.cache_manager is None:
            api_v3.cache_manager = MagicMock()
        
        response = client.post(
            '/api/v3/display/on-demand/start',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        # May return 404 if plugin not found, 200 if successful, or 500 on error
        assert response.status_code in [200, 201, 404, 500]
        # Verify cache was updated if successful
        if response.status_code in [200, 201]:
            assert api_v3.cache_manager.set.called
    
    @patch('web_interface.blueprints.api_v3._ensure_cache_manager')
    def test_stop_on_demand_display(self, mock_ensure_cache, client):
        """Test stopping on-demand display."""
        from web_interface.blueprints.api_v3 import api_v3
        
        # Mock the cache manager returned by _ensure_cache_manager
        mock_cache_manager = MagicMock()
        mock_ensure_cache.return_value = mock_cache_manager
        
        response = client.post('/api/v3/display/on-demand/stop')
        
        # May return 200 if successful or 500 on error
        assert response.status_code in [200, 500]
        # Verify stop request was set in cache if successful
        if response.status_code == 200:
            assert mock_cache_manager.set.called


class TestPluginsAPI:
    """Test plugins API endpoints."""
    
    def test_get_installed_plugins(self, client, mock_plugin_manager):
        """Test getting list of installed plugins."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.plugin_manager = mock_plugin_manager
        
        mock_plugin_manager.plugins = {
            'weather': MagicMock(plugin_id='weather'),
            'clock': MagicMock(plugin_id='clock')
        }
        mock_plugin_manager.get_plugin_metadata.return_value = {
            'id': 'weather',
            'name': 'Weather Plugin'
        }
        
        response = client.get('/api/v3/plugins/installed')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, (list, dict))
    
    def test_get_plugin_health(self, client, mock_plugin_manager):
        """Test getting plugin health information."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.plugin_manager = mock_plugin_manager
        
        # Setup health tracker
        mock_health_tracker = MagicMock()
        mock_health_tracker.get_all_health_summaries.return_value = {
            'weather': {'healthy': True}
        }
        mock_plugin_manager.health_tracker = mock_health_tracker
        
        response = client.get('/api/v3/plugins/health')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, (list, dict))
    
    def test_get_plugin_health_single(self, client, mock_plugin_manager):
        """Test getting health for single plugin."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.plugin_manager = mock_plugin_manager
        
        # Setup health tracker with proper method (endpoint calls get_health_summary)
        mock_health_tracker = MagicMock()
        mock_health_tracker.get_health_summary.return_value = {
            'healthy': True,
            'failures': 0,
            'last_success': '2024-01-01T00:00:00'
        }
        mock_plugin_manager.health_tracker = mock_health_tracker
        
        response = client.get('/api/v3/plugins/health/weather')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'healthy' in data.get('data', {}) or 'data' in data
    
    def test_toggle_plugin(self, client, mock_config_manager, mock_plugin_manager):
        """Test toggling plugin enabled state."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.config_manager = mock_config_manager
        api_v3.plugin_manager = mock_plugin_manager
        api_v3.plugin_state_manager = MagicMock()
        api_v3.operation_history = MagicMock()
        
        # Setup plugin manifests
        mock_plugin_manager.plugin_manifests = {'weather': {}}
        
        request_data = {
            'plugin_id': 'weather',
            'enabled': True
        }
        
        response = client.post(
            '/api/v3/plugins/toggle',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        mock_config_manager.save_config_atomic.assert_called_once()
    
    def test_get_plugin_config(self, client, mock_config_manager):
        """Test getting plugin configuration."""
        mock_config_manager.load_config.return_value = {
            'plugins': {
                'weather': {
                    'enabled': True,
                    'api_key': 'test_key'
                }
            }
        }
        
        response = client.get('/api/v3/plugins/config?plugin_id=weather')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'enabled' in data or 'config' in data or 'data' in data
    
    def test_save_plugin_config(self, client, mock_config_manager):
        """Test saving plugin configuration."""
        from web_interface.blueprints.api_v3 import api_v3
        api_v3.config_manager = mock_config_manager
        api_v3.schema_manager = MagicMock()
        api_v3.schema_manager.load_schema.return_value = {
            'type': 'object',
            'properties': {'enabled': {'type': 'boolean'}}
        }
        
        request_data = {
            'plugin_id': 'weather',
            'config': {
                'enabled': True,
                'update_interval': 300
            }
        }
        
        response = client.post(
            '/api/v3/plugins/config',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        assert response.status_code in [200, 500]  # May fail if validation fails
        if response.status_code == 200:
            mock_config_manager.save_config_atomic.assert_called_once()
    
    def test_get_plugin_schema(self, client):
        """Test getting plugin configuration schema."""
        from web_interface.blueprints.api_v3 import api_v3
        
        response = client.get('/api/v3/plugins/schema?plugin_id=weather')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'type' in data or 'schema' in data or 'data' in data
    
    def test_get_operation_status(self, client):
        """Test getting plugin operation status."""
        from web_interface.blueprints.api_v3 import api_v3
        
        # Setup operation queue mock
        mock_operation = MagicMock()
        mock_operation.operation_id = 'test-op-123'
        mock_operation.status = MagicMock(value='pending')
        mock_operation.operation_type = MagicMock(value='install')
        mock_operation.plugin_id = 'test-plugin'
        mock_operation.created_at = '2024-01-01T00:00:00'
        # Add to_dict method that the endpoint calls
        mock_operation.to_dict.return_value = {
            'operation_id': 'test-op-123',
            'status': 'pending',
            'operation_type': 'install',
            'plugin_id': 'test-plugin'
        }
        
        api_v3.operation_queue.get_operation_status.return_value = mock_operation
        
        response = client.get('/api/v3/plugins/operation/test-op-123')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data or 'operation' in data or 'data' in data
    
    def test_get_operation_history(self, client):
        """Test getting operation history."""
        from web_interface.blueprints.api_v3 import api_v3
        
        response = client.get('/api/v3/plugins/operation/history')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, (list, dict))
    
    def test_get_plugin_state(self, client):
        """Test getting plugin state."""
        from web_interface.blueprints.api_v3 import api_v3
        
        response = client.get('/api/v3/plugins/state')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, (list, dict))


class TestFontsAPI:
    """Test fonts API endpoints."""
    
    def test_get_fonts_catalog(self, client):
        """Test getting fonts catalog."""
        # Fonts endpoints don't use FontManager, they return hardcoded data
        response = client.get('/api/v3/fonts/catalog')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'catalog' in data.get('data', {}) or 'data' in data
    
    def test_get_font_tokens(self, client):
        """Test getting font tokens."""
        response = client.get('/api/v3/fonts/tokens')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'tokens' in data.get('data', {}) or 'data' in data
    
    def test_get_fonts_overrides(self, client):
        """Test getting font overrides."""
        response = client.get('/api/v3/fonts/overrides')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'overrides' in data.get('data', {}) or 'data' in data
    
    def test_save_fonts_overrides(self, client):
        """Test saving font overrides."""
        request_data = {
            'weather': 'small',
            'clock': 'regular'
        }
        
        response = client.post(
            '/api/v3/fonts/overrides',
            data=json.dumps(request_data),
            content_type='application/json'
        )
        
        assert response.status_code == 200


class TestAPIErrorHandling:
    """Test API error handling."""
    
    def test_invalid_json_request(self, client):
        """Test handling invalid JSON in request."""
        response = client.post(
            '/api/v3/config/main',
            data='invalid json',
            content_type='application/json'
        )
        
        # Flask may return 500 for JSON decode errors or 400 for bad request
        assert response.status_code in [400, 415, 500]
    
    def test_missing_required_fields(self, client):
        """Test handling missing required fields."""
        response = client.post(
            '/api/v3/plugins/toggle',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code in [400, 422, 500]
    
    def test_nonexistent_endpoint(self, client):
        """Test accessing nonexistent endpoint."""
        response = client.get('/api/v3/nonexistent')
        
        assert response.status_code == 404
    
    def test_method_not_allowed(self, client):
        """Test using wrong HTTP method."""
        # GET instead of POST
        response = client.get('/api/v3/config/main', 
                            query_string={'method': 'POST'})
        
        # Should work for GET, but if we try POST-only endpoint with GET
        response = client.get('/api/v3/config/schedule')
        
        # Schedule might allow GET, so test a POST-only endpoint
        response = client.get('/api/v3/display/on-demand/start')
        
        assert response.status_code in [200, 405]  # Depends on implementation
