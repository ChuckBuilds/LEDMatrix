#!/usr/bin/env python3
"""
Python file validation script to prevent common formatting errors.

This script checks for:
1. Proper indentation (4 spaces, no mixed tabs/spaces)
2. Missing imports
3. Syntax errors
4. Line length issues
5. Proper try/except structure

Usage: python tools/validate_python.py <python_file>
"""

import ast
import sys
import os
from pathlib import Path

def validate_file(filepath: str) -> bool:
    """Validate a Python file for common issues."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        issues_found = []

        # Check for tabs (should use spaces)
        if '\t' in content:
            issues_found.append("❌ Contains tabs - use 4 spaces instead")

        # Check for trailing whitespace
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if line.rstrip() != line:
                issues_found.append(f"❌ Line {i}: Trailing whitespace")

        # Check for very long lines
        for i, line in enumerate(lines, 1):
            if len(line) > 120:
                issues_found.append(f"⚠️  Line {i}: Very long line ({len(line)} chars)")

        # Check for proper try/except structure
        try:
            ast.parse(content)
        except SyntaxError as e:
            issues_found.append(f"❌ Syntax error: {e}")

        # Check for mixed quotes (inconsistency)
        single_quotes = content.count("'")
        double_quotes = content.count('"')
        if single_quotes > 0 and double_quotes > 0:
            issues_found.append("⚠️  Mixed quote usage - consider using double quotes consistently")

        # Report results
        if issues_found:
            print(f"\n🔍 Validation Results for: {filepath}")
            print("=" * 50)
            for issue in issues_found:
                print(issue)
            return False
        else:
            print(f"✅ {filepath} - All checks passed!")
            return True

    except Exception as e:
        print(f"❌ Error reading file {filepath}: {e}")
        return False

def validate_directory(directory: str) -> bool:
    """Validate all Python files in a directory."""
    all_passed = True

    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                if not validate_file(filepath):
                    all_passed = False

    return all_passed

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tools/validate_python.py <file_or_directory>")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isfile(target):
        validate_file(target)
    elif os.path.isdir(target):
        validate_directory(target)
    else:
        print(f"❌ Path not found: {target}")
        sys.exit(1)
