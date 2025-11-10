# Static Image Plugin - Multi-Image Upload & Rotation Implementation Plan

## Overview

Enhance the static-image plugin to support:
1. **Multiple image uploads** via web UI
2. **Image rotation** (sequential, random, time-based, date-based)
3. **Robust asset management** (storage, validation, cleanup)
4. **Future-proof architecture** for advanced rotation logic

## Architecture Design

### 1. Configuration Schema Enhancement

#### Current Schema
```json
{
  "image_path": "assets/static_images/default.png"
}
```

#### Enhanced Schema (Backward Compatible)
```json
{
  "image_config": {
    "mode": "single" | "multiple",
    "rotation_mode": "sequential" | "random" | "time_based" | "date_based",
    "images": [
      {
        "id": "uuid-or-hash",
        "path": "assets/plugins/static-image/uploads/image_1234567890.png",
        "uploaded_at": "2025-01-15T10:30:00Z",
        "display_order": 0,
        "schedule": null  // Future: {"start_time": "08:00", "end_time": "18:00", "days": [1,2,3,4,5]}
      }
    ]
  },
  
  // Legacy support - maps to single image mode
  "image_path": "assets/static_images/default.png",
  
  // Rotation settings
  "rotation_settings": {
    "sequential_loop": true,
    "random_seed": null,  // null = use time, or fixed seed for reproducible rotation
    "time_intervals": {
      "enabled": false,
      "interval_seconds": 3600  // Change image every hour
    },
    "date_ranges": []  // Future: [{"start": "2025-12-01", "end": "2025-12-25", "image_id": "..."}]
  }
}
```

### 2. Asset Storage Structure

```
assets/
├── plugins/
│   └── static-image/
│       └── uploads/
│           ├── image_1705312200_abc123.png
│           ├── image_1705312400_def456.jpg
│           └── .metadata.json  // Maps IDs to filenames
```

**Storage Strategy:**
- Files stored in `assets/plugins/static-image/uploads/`
- Filenames: `image_{timestamp}_{hash}.{ext}` (prevents collisions)
- Metadata JSON tracks: ID → filename mapping, upload dates, file sizes
- Cleanup: Remove files not referenced in config

### 3. Backend API Endpoints

#### POST `/api/v3/plugins/assets/upload`
**Purpose:** Upload image files for a specific plugin

**Request:**
- `multipart/form-data`
- `plugin_id`: string (required)
- `files`: File[] (multiple files supported)
- `rotation_mode`: string (optional, default: "sequential")

**Response:**
```json
{
  "status": "success",
  "uploaded_files": [
    {
      "id": "uuid-here",
      "filename": "image_1705312200_abc123.png",
      "path": "assets/plugins/static-image/uploads/image_1705312200_abc123.png",
      "size": 45678,
      "uploaded_at": "2025-01-15T10:30:00Z"
    }
  ]
}
```

**Validation:**
- File type: PNG, JPG, JPEG, BMP, GIF
- Max file size: 5MB per file
- Max files per upload: 10
- Total storage limit: 50MB per plugin

#### DELETE `/api/v3/plugins/assets/delete`
**Purpose:** Delete uploaded image

**Request:**
- `plugin_id`: string
- `image_id`: string (from upload response)

**Response:**
```json
{
  "status": "success",
  "deleted_file": "image_1705312200_abc123.png"
}
```

#### GET `/api/v3/plugins/assets/list`
**Purpose:** List all uploaded images for a plugin

**Response:**
```json
{
  "status": "success",
  "images": [
    {
      "id": "uuid-here",
      "filename": "image_1705312200_abc123.png",
      "path": "assets/plugins/static-image/uploads/image_1705312200_abc123.png",
      "size": 45678,
      "uploaded_at": "2025-01-15T10:30:00Z"
    }
  ]
}
```

### 4. Frontend Form Generator Enhancement

#### Schema Format for File Upload
```json
{
  "type": "object",
  "properties": {
    "images": {
      "type": "array",
      "x-widget": "file-upload",
      "x-upload-config": {
        "endpoint": "/api/v3/plugins/assets/upload",
        "plugin_id_field": "plugin_id",
        "max_files": 10,
        "allowed_types": ["image/png", "image/jpeg", "image/bmp", "image/gif"],
        "max_size_mb": 5
      },
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "string"},
          "path": {"type": "string"},
          "uploaded_at": {"type": "string", "format": "date-time"}
        }
      },
      "description": "Upload images to display. Multiple images will rotate based on rotation mode."
    },
    "rotation_mode": {
      "type": "string",
      "enum": ["sequential", "random", "time_based", "date_based"],
      "default": "sequential",
      "description": "How to rotate through images"
    }
  }
}
```

#### UI Components
1. **File Upload Widget:**
   - Drag-and-drop zone
   - File list with thumbnails
   - Remove button per file
   - Upload progress indicator
   - Image preview before upload

2. **Rotation Mode Selector:**
   - Dropdown with rotation options
   - Settings panel per mode:
     - Sequential: Loop option
     - Random: Seed option
     - Time-based: Interval input
     - Date-based: Calendar picker (future)

### 5. Plugin Manager Updates

#### Rotation Logic in `manager.py`

```python
class StaticImagePlugin(BasePlugin):
    def __init__(self, ...):
        # ... existing code ...
        
        # Enhanced image handling
        self.image_config = config.get('image_config', {})
        self.rotation_mode = self.image_config.get('rotation_mode', 'sequential')
        self.rotation_settings = config.get('rotation_settings', {})
        self.images_list = self.image_config.get('images', [])
        self.current_image_index = 0
        self.last_rotation_time = time.time()
        
        # Initialize rotation
        self._setup_rotation()
    
    def _setup_rotation(self):
        """Initialize rotation based on mode"""
        if self.rotation_mode == 'random':
            import random
            seed = self.rotation_settings.get('random_seed')
            if seed:
                random.seed(seed)
        
        if not self.images_list:
            # Fallback to legacy image_path
            if self.image_path:
                self.images_list = [{'path': self.image_path}]
    
    def _get_next_image(self) -> Optional[str]:
        """Get next image path based on rotation mode"""
        if not self.images_list:
            return None
        
        if self.rotation_mode == 'sequential':
            path = self.images_list[self.current_image_index]['path']
            self.current_image_index = (self.current_image_index + 1) % len(self.images_list)
            return path
        
        elif self.rotation_mode == 'random':
            import random
            return random.choice(self.images_list)['path']
        
        elif self.rotation_mode == 'time_based':
            interval = self.rotation_settings.get('time_intervals', {}).get('interval_seconds', 3600)
            now = time.time()
            if now - self.last_rotation_time >= interval:
                self.current_image_index = (self.current_image_index + 1) % len(self.images_list)
                self.last_rotation_time = now
            return self.images_list[self.current_image_index]['path']
        
        elif self.rotation_mode == 'date_based':
            # Future implementation
            return self._get_date_based_image()
        
        return self.images_list[0]['path']
    
    def display(self, force_clear: bool = False):
        """Display current image based on rotation"""
        image_path = self._get_next_image()
        if not image_path or not os.path.exists(image_path):
            self._display_error()
            return
        
        self.image_path = image_path  # For compatibility
        self._load_image()
        
        # ... rest of display logic ...
```

### 6. Asset Management System

#### File Operations
- **Upload:** Save to `assets/plugins/{plugin_id}/uploads/`
- **Validation:** Check file type, size, dimensions
- **Metadata:** Track in `.metadata.json`
- **Cleanup:** Remove orphaned files on config save
- **Permissions:** Ensure writable by web service

#### Security
- Validate file extensions (whitelist)
- Check file content (magic bytes, not just extension)
- Limit file sizes
- Sanitize filenames
- Prevent path traversal

### 7. Migration Strategy

#### Backward Compatibility
1. **Legacy Support:**
   - If `image_path` exists but no `image_config`, auto-convert
   - Create `image_config` with single image from `image_path`

2. **Config Migration:**
```python
def _migrate_legacy_config(self, config):
    """Migrate legacy image_path to new image_config format"""
    if 'image_path' in config and 'image_config' not in config:
        config['image_config'] = {
            'mode': 'single',
            'rotation_mode': 'sequential',
            'images': [{
                'id': str(uuid.uuid4()),
                'path': config['image_path'],
                'uploaded_at': datetime.now().isoformat(),
                'display_order': 0
            }]
        }
    return config
```

## Implementation Phases

### Phase 1: Core Upload System
1. ✅ Enhanced config schema
2. ✅ Backend upload endpoint
3. ✅ Asset storage structure
4. ✅ File validation

### Phase 2: Frontend Integration
5. ✅ File upload widget in form generator
6. ✅ Image preview/management UI
7. ✅ Rotation mode selector

### Phase 3: Plugin Rotation Logic
8. ✅ Update plugin manager with rotation
9. ✅ Sequential rotation
10. ✅ Random rotation

### Phase 4: Advanced Features
11. ✅ Time-based rotation
12. ✅ Date-based rotation (future)
13. ✅ Cleanup/orphan removal

## File Structure Changes

```
plugins/static-image/
├── manager.py              # Enhanced with rotation logic
├── config_schema.json      # Updated with upload/rotation fields
├── manifest.json           # No changes
└── README.md               # Update documentation

web_interface/
├── blueprints/
│   └── api_v3.py           # Add upload/delete/list endpoints
└── templates/v3/
    └── partials/
        └── plugins.html     # File upload widget

assets/
└── plugins/
    └── static-image/
        └── uploads/        # NEW - user uploaded images
            └── .metadata.json
```

## Testing Checklist

- [ ] Single image upload works
- [ ] Multiple image upload works
- [ ] File validation (type, size)
- [ ] Sequential rotation cycles correctly
- [ ] Random rotation works
- [ ] Time-based rotation changes at intervals
- [ ] Legacy config migration preserves existing images
- [ ] Orphaned file cleanup on config save
- [ ] Web UI displays upload widget correctly
- [ ] Image preview shows before upload
- [ ] Delete removes file and updates config
- [ ] Error handling for missing/invalid files

## Future Enhancements

1. **Date-based rotation:** Display different images on specific dates
2. **Time-of-day rotation:** Show images based on time ranges
3. **Transition effects:** Fade between images
4. **Image filters:** Apply effects (brightness, contrast)
5. **Bulk operations:** Select multiple images for deletion
6. **Image organization:** Folders/tags for images
7. **Remote images:** Support URLs (with caching)

