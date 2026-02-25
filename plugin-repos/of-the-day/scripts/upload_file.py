#!/usr/bin/env python3
"""
Upload a JSON file to the of_the_day directory.
Validates the file format and automatically adds category to config.
"""

import os
import json
import sys
from pathlib import Path

# Get plugin directory (scripts/ -> plugin root)
plugin_dir = Path(__file__).parent.parent
data_dir = plugin_dir / 'of_the_day'
data_dir.mkdir(parents=True, exist_ok=True)

# Read JSON from stdin
try:
    input_data = json.load(sys.stdin)
    filename = input_data.get('filename', '')
    content = input_data.get('content', '')
    
    if not filename or not content:
        print(json.dumps({
            'status': 'error',
            'message': 'Filename and content are required'
        }))
        sys.exit(1)
    
    # Validate filename
    if not filename.endswith('.json'):
        print(json.dumps({
            'status': 'error',
            'message': 'File must be a JSON file (.json)'
        }))
        sys.exit(1)

    if '..' in filename or '/' in filename or '\\' in filename:
        print(json.dumps({
            'status': 'error',
            'message': 'Invalid filename'
        }))
        sys.exit(1)

    # Validate JSON content
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        print(json.dumps({
            'status': 'error',
            'message': f'Invalid JSON: {str(e)}'
        }))
        sys.exit(1)
    
    # Validate structure (must be an object with day number keys 1-365)
    if not isinstance(data, dict):
        print(json.dumps({
            'status': 'error',
            'message': 'JSON must be an object with day numbers (1-365) as keys'
        }))
        sys.exit(1)

    # Check if keys are valid day numbers
    for key in data.keys():
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
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Extract category name and update config
    category_name = filename.replace('.json', '')
    display_name = input_data.get('display_name', category_name.replace('_', ' ').title())
    
    # Update config
    sys.path.insert(0, str(plugin_dir))
    from scripts.update_config import add_category_to_config
    add_category_to_config(category_name, f'of_the_day/{filename}', display_name)
    
    print(json.dumps({
        'status': 'success',
        'message': f'File {filename} uploaded successfully',
        'filename': filename,
        'category_name': category_name
    }))
    
except Exception as e:
    print(json.dumps({
        'status': 'error',
        'message': str(e)
    }))
    sys.exit(1)

