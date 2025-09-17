# Background Data Service for LEDMatrix

## Overview

The Background Data Service is a new feature that implements background threading for season data fetching to prevent blocking the main display loop. This significantly improves responsiveness and user experience during data fetching operations.

## Key Benefits

- **Non-blocking**: Season data fetching no longer blocks the main display loop
- **Immediate Response**: Returns cached or partial data immediately while fetching complete data in background
- **Configurable**: Can be enabled/disabled per sport with customizable settings
- **Thread-safe**: Uses proper synchronization for concurrent access
- **Retry Logic**: Automatic retry with exponential backoff for failed requests
- **Progress Tracking**: Comprehensive logging and statistics

## Architecture

### Core Components

1. **BackgroundDataService**: Main service class managing background threads
2. **FetchRequest**: Represents individual fetch operations
3. **FetchResult**: Contains results of fetch operations
4. **Sport Managers**: Updated to use background service

### How It Works

1. **Cache Check**: First checks for cached data and returns immediately if available
2. **Background Fetch**: If no cache, starts background thread to fetch complete season data
3. **Partial Data**: Returns immediate partial data (current/recent games) for quick display
4. **Completion**: Background fetch completes and caches full dataset
5. **Future Requests**: Subsequent requests use cached data for instant response

## Configuration

### NFL Configuration Example

```json
{
    "nfl_scoreboard": {
        "enabled": true,
        "background_service": {
            "enabled": true,
            "max_workers": 3,
            "request_timeout": 30,
            "max_retries": 3,
            "priority": 2
        }
    }
}
```

### Configuration Options

- **enabled**: Enable/disable background service (default: true)
- **max_workers**: Maximum number of background threads (default: 3)
- **request_timeout**: HTTP request timeout in seconds (default: 30)
- **max_retries**: Maximum retry attempts for failed requests (default: 3)
- **priority**: Request priority (higher = more important, default: 2)

## Implementation Status

### Phase 1: Background Season Data Fetching ✅ COMPLETED

- [x] Created BackgroundDataService class
- [x] Implemented thread-safe data caching
- [x] Added retry logic with exponential backoff
- [x] Modified NFL manager to use background service
- [x] Added configuration support
- [x] Created test script

### Phase 2: Rollout to Other Sports (Next Steps)

- [ ] Apply to NCAAFB manager
- [ ] Apply to NBA manager
- [ ] Apply to NHL manager
- [ ] Apply to MLB manager
- [ ] Apply to other sport managers

## Testing

### Test Script

Run the test script to verify background service functionality:

```bash
python test_background_service.py
```

### Test Scenarios

1. **Cache Hit**: Verify immediate return of cached data
2. **Background Fetch**: Verify non-blocking background data fetching
3. **Partial Data**: Verify immediate return of partial data during background fetch
4. **Completion**: Verify background fetch completion and caching
5. **Subsequent Requests**: Verify cache usage for subsequent requests
6. **Service Disabled**: Verify fallback to synchronous fetching

### Expected Results

- Initial fetch should return partial data immediately (< 1 second)
- Background fetch should complete within 10-30 seconds
- Subsequent fetches should use cache (< 0.1 seconds)
- No blocking of main display loop

## Performance Impact

### Before Background Service
- Season data fetch: 10-30 seconds (blocking)
- Display loop: Frozen during fetch
- User experience: Poor responsiveness

### After Background Service
- Initial response: < 1 second (partial data)
- Background fetch: 10-30 seconds (non-blocking)
- Display loop: Continues normally
- User experience: Excellent responsiveness

## Monitoring

### Logs

The service provides comprehensive logging:

```
[NFL] Background service enabled with 3 workers
[NFL] Starting background fetch for 2024 season schedule...
[NFL] Using 15 immediate events while background fetch completes
[NFL] Background fetch completed for 2024: 256 events
```

### Statistics

Access service statistics:

```python
stats = background_service.get_statistics()
print(f"Total requests: {stats['total_requests']}")
print(f"Cache hits: {stats['cached_hits']}")
print(f"Average fetch time: {stats['average_fetch_time']:.2f}s")
```

## Error Handling

### Automatic Retry
- Failed requests are automatically retried with exponential backoff
- Maximum retry attempts are configurable
- Failed requests are logged with error details

### Fallback Behavior
- If background service is disabled, falls back to synchronous fetching
- If background fetch fails, returns partial data if available
- Graceful degradation ensures system continues to function

## Future Enhancements

### Phase 2 Features
- Apply to all sport managers
- Priority-based request queuing
- Dynamic worker scaling
- Request batching for efficiency

### Phase 3 Features
- Real-time data streaming
- WebSocket support for live updates
- Advanced caching strategies
- Performance analytics dashboard

## Troubleshooting

### Common Issues

1. **Background service not starting**
   - Check configuration: `background_service.enabled = true`
   - Verify cache manager is properly initialized
   - Check logs for initialization errors

2. **Slow background fetches**
   - Increase `request_timeout` in configuration
   - Check network connectivity
   - Monitor API rate limits

3. **Memory usage**
   - Background service automatically cleans up old requests
   - Adjust `max_workers` if needed
   - Monitor cache size

### Debug Mode

Enable debug logging for detailed information:

```python
logging.getLogger('src.background_data_service').setLevel(logging.DEBUG)
```

## Contributing

When adding background service support to new sport managers:

1. Import the background service
2. Initialize in `__init__` method
3. Update data fetching method to use background service
4. Add configuration options
5. Test thoroughly
6. Update documentation

## License

This feature is part of the LEDMatrix project and follows the same license terms.
