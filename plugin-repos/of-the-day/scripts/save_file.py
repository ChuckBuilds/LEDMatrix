#!/usr/bin/env python3
"""
Save updated content to a JSON file in the of_the_day directory.
Validates the JSON structure before saving.
"""

import os
import json
import sys
from pathlib import Path

# Get plugin directory (scripts/ -> plugin root)
plugin_dir = Path(__file__).parent.parent
data_dir = plugin_dir / 'of_the_day'
data_dir.mkdir(parents=True, exist_ok=True)

try:
    input_data = json.load(sys.stdin)
    filename = input_data.get('filename', '')
    content_str = input_data.get('content', '')
    
    if not filename or not content_str:
        print(json.dumps({
            'status': 'error',
            'message': 'Filename and content are required'
        }))
        sys.exit(1)
    
    # Security: ensure filename doesn't contain path traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        print(json.dumps({
            'status': 'error',
            'message': 'Invalid filename'
        }))
        sys.exit(1)

    if not filename.endswith('.json'):
        print(json.dumps({
            'status': 'error',
            'message': 'File must be a JSON file (.json)'
        }))
        sys.exit(1)

    # Validate JSON
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError as e:
        print(json.dumps({
            'status': 'error',
            'message': f'Invalid JSON: {str(e)}'
        }))
        sys.exit(1)
    
    # Validate structure (must be an object with day number keys 1-365)
    if not isinstance(content, dict):
        print(json.dumps({
            'status': 'error',
            'message': 'JSON must be an object with day numbers (1-365) as keys'
        }))
        sys.exit(1)

    # Check if keys are valid day numbers
    for key in content.keys():
        try:
            day_num = int(key)
            if day_num < 1 or day_num > 365:
                print(json.dumps({
                    'status': 'error',
                    'message': f'Day number {day_num} is out of range (must be 1-365)'
                }))
                sys.exit(1)
        except ValueError:
            print(json.dumps({
                'status': 'error',
                'message': f'Invalid key "{key}": all keys must be day numbers (1-365)'
            }))
            sys.exit(1)
    
    # Save file
    file_path = data_dir / filename
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(content, f, indent=2, ensure_ascii=False)
    
    print(json.dumps({
        'status': 'success',
        'message': f'File {filename} saved successfully'
    }))
    
except Exception as e:
    print(json.dumps({
        'status': 'error',
        'message': str(e)
    }))
    sys.exit(1)

