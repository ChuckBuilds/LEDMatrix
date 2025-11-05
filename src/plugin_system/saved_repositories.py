"""
Saved Repositories Manager for LEDMatrix

Manages saved GitHub repository URLs for easy plugin discovery and installation.
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Dict, Optional


class SavedRepositoriesManager:
    """Manages saved GitHub repository URLs."""
    
    def __init__(self, config_path: str = "config/saved_repositories.json"):
        """
        Initialize the saved repositories manager.
        
        Args:
            config_path: Path to JSON file storing saved repositories
        """
        self.config_path = Path(config_path)
        self.logger = logging.getLogger(__name__)
        self.repositories = self._load_repositories()
    
    def _load_repositories(self) -> List[Dict[str, str]]:
        """Load saved repositories from file."""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                    # Ensure it's a list
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and 'repositories' in data:
                        return data['repositories']
                    else:
                        return []
            return []
        except Exception as e:
            self.logger.error(f"Error loading saved repositories: {e}")
            return []
    
    def _save_repositories(self) -> bool:
        """Save repositories to file."""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, 'w') as f:
                json.dump(self.repositories, f, indent=2)
            
            self.logger.info(f"Saved {len(self.repositories)} repositories to {self.config_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving repositories: {e}")
            return False
    
    def get_all(self) -> List[Dict[str, str]]:
        """Get all saved repositories."""
        return self.repositories.copy()
    
    def add(self, repo_url: str, name: Optional[str] = None) -> bool:
        """
        Add a repository to saved list.
        
        Args:
            repo_url: GitHub repository URL
            name: Optional friendly name for the repository
            
        Returns:
            True if added successfully
        """
        # Clean URL
        repo_url = repo_url.strip().rstrip('/').replace('.git', '')
        
        # Check if already exists
        for repo in self.repositories:
            if repo.get('url') == repo_url:
                self.logger.warning(f"Repository already exists: {repo_url}")
                return False
        
        # Extract name from URL if not provided
        if not name:
            parts = repo_url.split('/')
            if len(parts) >= 2:
                name = parts[-1]
            else:
                name = repo_url
        
        # Add repository
        self.repositories.append({
            'url': repo_url,
            'name': name,
            'type': 'registry' if 'plugins.json' in repo_url or 'ledmatrix-plugins' in repo_url.lower() else 'single'
        })
        
        return self._save_repositories()
    
    def remove(self, repo_url: str) -> bool:
        """
        Remove a repository from saved list.
        
        Args:
            repo_url: GitHub repository URL to remove
            
        Returns:
            True if removed successfully
        """
        # Clean URL
        repo_url = repo_url.strip().rstrip('/').replace('.git', '')
        
        original_count = len(self.repositories)
        self.repositories = [r for r in self.repositories if r.get('url') != repo_url]
        
        if len(self.repositories) < original_count:
            return self._save_repositories()
        else:
            self.logger.warning(f"Repository not found: {repo_url}")
            return False
    
    def has(self, repo_url: str) -> bool:
        """Check if a repository is already saved."""
        repo_url = repo_url.strip().rstrip('/').replace('.git', '')
        return any(r.get('url') == repo_url for r in self.repositories)
    
    def get_registry_repositories(self) -> List[Dict[str, str]]:
        """Get only registry-style repositories."""
        return [r for r in self.repositories if r.get('type') == 'registry']

