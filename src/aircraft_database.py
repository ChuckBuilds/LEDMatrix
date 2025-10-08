"""
Aircraft Database Manager - Offline aircraft information lookup

This module provides offline aircraft information from the FAA Aircraft Registry
and other public sources, reducing the need for API calls.

Data Sources:
- FAA Aircraft Registry: https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download
- OpenSky Network: https://opensky-network.org/datasets/metadata/
"""

import logging
import sqlite3
import csv
import requests
import zipfile
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime, timedelta
import io

logger = logging.getLogger(__name__)


class AircraftDatabase:
    """Manages offline aircraft database for tail number lookups."""
    
    # FAA Aircraft Registry URLs
    FAA_MASTER_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
    FAA_ACFTREF_URL = "https://registry.faa.gov/database/ARData.zip"  # Aircraft reference data
    
    # OpenSky Network - community-maintained aircraft database
    OPENSKY_DB_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    
    def __init__(self, cache_dir: Path):
        """Initialize aircraft database.
        
        Args:
            cache_dir: Directory to store database and downloaded files
        """
        self.cache_dir = Path(cache_dir) / 'aircraft_db'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = self.cache_dir / 'aircraft.db'
        self.last_update_file = self.cache_dir / 'last_update.txt'
        
        # Database refresh interval (30 days - FAA updates monthly)
        self.refresh_interval_days = 30
        
        # Initialize database
        self._init_database()
        
        # Check if we need to update
        if self._should_update():
            logger.info("[Aircraft DB] Database needs update, downloading latest data...")
            self.update_database()
        else:
            logger.info("[Aircraft DB] Using existing database")
    
    def _init_database(self):
        """Initialize SQLite database schema."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Aircraft registration table (from FAA and OpenSky)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS aircraft (
                    icao24 TEXT PRIMARY KEY,
                    registration TEXT,
                    manufacturer TEXT,
                    model TEXT,
                    type_aircraft TEXT,
                    type_engine TEXT,
                    serial_number TEXT,
                    operator TEXT,
                    owner_name TEXT,
                    last_updated TIMESTAMP,
                    source TEXT
                )
            ''')
            
            # Create indexes for fast lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_registration ON aircraft(registration)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_icao24 ON aircraft(icao24)')
            
            # Aircraft type reference table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS aircraft_types (
                    type_code TEXT PRIMARY KEY,
                    manufacturer TEXT,
                    model TEXT,
                    type_aircraft TEXT,
                    type_engine TEXT,
                    category TEXT,
                    num_engines INTEGER,
                    num_seats INTEGER
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"[Aircraft DB] Database initialized at {self.db_path}")
            
        except Exception as e:
            logger.error(f"[Aircraft DB] Failed to initialize database: {e}")
    
    def _should_update(self) -> bool:
        """Check if database needs updating."""
        # Check if database exists and has data
        if not self.db_path.exists():
            return True
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM aircraft')
            count = cursor.fetchone()[0]
            conn.close()
            
            if count == 0:
                logger.info("[Aircraft DB] Database is empty, needs update")
                return True
        except:
            return True
        
        # Check last update time
        if not self.last_update_file.exists():
            return True
        
        try:
            last_update = datetime.fromtimestamp(self.last_update_file.stat().st_mtime)
            days_since_update = (datetime.now() - last_update).days
            
            if days_since_update >= self.refresh_interval_days:
                logger.info(f"[Aircraft DB] Database is {days_since_update} days old, needs update")
                return True
            
            logger.info(f"[Aircraft DB] Database is {days_since_update} days old, still fresh")
            return False
            
        except Exception as e:
            logger.warning(f"[Aircraft DB] Error checking update time: {e}")
            return True
    
    def update_database(self, force: bool = False) -> bool:
        """Download and update aircraft database.
        
        Args:
            force: Force update even if database is fresh
            
        Returns:
            True if update was successful
        """
        if not force and not self._should_update():
            logger.info("[Aircraft DB] Database is up to date")
            return True
        
        logger.info("[Aircraft DB] Starting database update...")
        
        success = False
        
        # Try FAA database first (most comprehensive for US aircraft)
        try:
            logger.info("[Aircraft DB] Downloading FAA Aircraft Registry...")
            success = self._update_from_faa()
        except Exception as e:
            logger.warning(f"[Aircraft DB] Failed to update from FAA: {e}")
        
        # Try OpenSky Network as fallback/supplement
        if not success:
            try:
                logger.info("[Aircraft DB] Downloading OpenSky Network database...")
                success = self._update_from_opensky()
            except Exception as e:
                logger.warning(f"[Aircraft DB] Failed to update from OpenSky: {e}")
        
        if success:
            # Update last update timestamp
            self.last_update_file.write_text(str(datetime.now().timestamp()))
            logger.info("[Aircraft DB] Database update completed successfully")
        else:
            logger.error("[Aircraft DB] All database update sources failed")
        
        return success
    
    def _update_from_faa(self) -> bool:
        """Update database from FAA Aircraft Registry."""
        try:
            # Download FAA database (large file, need longer timeout)
            logger.info(f"[Aircraft DB] Downloading from {self.FAA_MASTER_URL}")
            logger.info("[Aircraft DB] This may take several minutes (50-80 MB download)...")
            
            # Use streaming download with longer timeout
            response = requests.get(self.FAA_MASTER_URL, timeout=300, stream=True)
            response.raise_for_status()
            
            # Log download progress
            total_size = int(response.headers.get('content-length', 0))
            if total_size > 0:
                logger.info(f"[Aircraft DB] Download size: {total_size / (1024*1024):.1f} MB")
            
            # Read content in chunks
            content = bytearray()
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks
            
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    content.extend(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:  # Log every 5MB
                        progress = (downloaded / total_size) * 100
                        logger.info(f"[Aircraft DB] Downloaded {downloaded / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB ({progress:.1f}%)")
            
            logger.info(f"[Aircraft DB] Download complete: {len(content) / (1024*1024):.1f} MB")
            
            # Extract ZIP file
            logger.info("[Aircraft DB] Extracting ZIP file...")
            zip_file = zipfile.ZipFile(io.BytesIO(bytes(content)))
            
            # Look for MASTER.txt (main aircraft registry)
            master_file = None
            for name in zip_file.namelist():
                if 'MASTER' in name.upper() and name.endswith('.txt'):
                    master_file = name
                    break
            
            if not master_file:
                logger.error("[Aircraft DB] MASTER.txt not found in FAA zip file")
                return False
            
            logger.info(f"[Aircraft DB] Processing {master_file}...")
            
            # Read and parse MASTER.txt
            with zip_file.open(master_file) as f:
                # FAA files are comma-delimited
                content = f.read().decode('utf-8', errors='ignore')
                reader = csv.reader(io.StringIO(content))
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                aircraft_count = 0
                
                for row in reader:
                    if len(row) < 10:
                        continue
                    
                    try:
                        # FAA MASTER.txt format (approximate columns):
                        # 0: N-Number (registration)
                        # 1: Serial Number
                        # 2: MFR MDL Code
                        # 3: ENG MFR MDL
                        # 4: Year MFR
                        # 5: Type Registrant
                        # 6: Name
                        # 7: Street
                        # 8: Street2
                        # 9: City
                        # etc...
                        
                        registration = row[0].strip()
                        serial_number = row[1].strip() if len(row) > 1 else ''
                        mfr_model_code = row[2].strip() if len(row) > 2 else ''
                        owner_name = row[6].strip() if len(row) > 6 else ''
                        
                        # Skip invalid entries
                        if not registration:
                            continue
                        
                        # Generate ICAO24 from N-number (US aircraft)
                        # N-numbers convert to ICAO hex: N12345 -> A12345 (approximately)
                        icao24 = self._registration_to_icao24(registration)
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO aircraft 
                            (icao24, registration, serial_number, owner_name, last_updated, source)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (
                            icao24,
                            registration,
                            serial_number,
                            owner_name,
                            datetime.now(),
                            'FAA'
                        ))
                        
                        aircraft_count += 1
                        
                        if aircraft_count % 10000 == 0:
                            logger.info(f"[Aircraft DB] Processed {aircraft_count} aircraft...")
                    
                    except Exception as e:
                        # Skip malformed rows
                        continue
                
                conn.commit()
                conn.close()
                
                logger.info(f"[Aircraft DB] Successfully imported {aircraft_count} aircraft from FAA")
                return aircraft_count > 0
                
        except Exception as e:
            logger.error(f"[Aircraft DB] Error updating from FAA: {e}")
            return False
    
    def _update_from_opensky(self) -> bool:
        """Update database from OpenSky Network."""
        try:
            logger.info(f"[Aircraft DB] Downloading from {self.OPENSKY_DB_URL}")
            logger.info("[Aircraft DB] This may take several minutes (30-50 MB download)...")
            
            # Use streaming download with longer timeout
            response = requests.get(self.OPENSKY_DB_URL, timeout=300, stream=True)
            response.raise_for_status()
            
            # Log download progress
            total_size = int(response.headers.get('content-length', 0))
            if total_size > 0:
                logger.info(f"[Aircraft DB] Download size: {total_size / (1024*1024):.1f} MB")
            
            # Read content in chunks
            content = bytearray()
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks
            
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    content.extend(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:  # Log every 5MB
                        progress = (downloaded / total_size) * 100
                        logger.info(f"[Aircraft DB] Downloaded {downloaded / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB ({progress:.1f}%)")
            
            logger.info(f"[Aircraft DB] Download complete: {len(content) / (1024*1024):.1f} MB")
            
            # Parse CSV
            logger.info("[Aircraft DB] Parsing CSV data...")
            csv_content = bytes(content).decode('utf-8', errors='ignore')
            reader = csv.DictReader(io.StringIO(csv_content))
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            aircraft_count = 0
            
            for row in reader:
                try:
                    icao24 = row.get('icao24', '').strip().lower()
                    registration = row.get('registration', '').strip()
                    manufacturer = row.get('manufacturername', '').strip()
                    model = row.get('model', '').strip()
                    operator = row.get('operator', '').strip()
                    serial_number = row.get('serialnumber', '').strip()
                    type_code = row.get('typecode', '').strip()
                    
                    if not icao24:
                        continue
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO aircraft 
                        (icao24, registration, manufacturer, model, serial_number, 
                         operator, type_aircraft, last_updated, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        icao24,
                        registration,
                        manufacturer,
                        model,
                        serial_number,
                        operator,
                        type_code,
                        datetime.now(),
                        'OpenSky'
                    ))
                    
                    aircraft_count += 1
                    
                    if aircraft_count % 10000 == 0:
                        logger.info(f"[Aircraft DB] Processed {aircraft_count} aircraft...")
                
                except Exception as e:
                    # Skip malformed rows
                    continue
            
            conn.commit()
            conn.close()
            
            logger.info(f"[Aircraft DB] Successfully imported {aircraft_count} aircraft from OpenSky")
            return aircraft_count > 0
            
        except Exception as e:
            logger.error(f"[Aircraft DB] Error updating from OpenSky: {e}")
            return False
    
    def _registration_to_icao24(self, registration: str) -> str:
        """Convert registration number to ICAO24 hex code (approximate).
        
        This is a simplified conversion. Real conversion is complex and country-specific.
        For US aircraft, N-numbers have a specific conversion algorithm.
        
        Args:
            registration: Aircraft registration (e.g., N12345)
            
        Returns:
            Approximate ICAO24 hex code
        """
        # For US aircraft starting with 'N', use a simplified conversion
        if registration.startswith('N'):
            # This is a very simplified conversion - real algorithm is more complex
            # In reality, you'd need a proper N-number to ICAO24 conversion table
            return 'a' + registration[1:].lower().zfill(5)
        
        # For other countries, return a placeholder
        return registration.lower().replace('-', '').zfill(6)[:6]
    
    def lookup_by_icao24(self, icao24: str) -> Optional[Dict]:
        """Look up aircraft by ICAO24 hex code.
        
        Args:
            icao24: ICAO24 hex code (e.g., 'a12345')
            
        Returns:
            Dictionary with aircraft information or None
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM aircraft WHERE icao24 = ? COLLATE NOCASE
            ''', (icao24.lower(),))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'icao24': row['icao24'],
                    'registration': row['registration'],
                    'manufacturer': row['manufacturer'],
                    'model': row['model'],
                    'type_aircraft': row['type_aircraft'],
                    'serial_number': row['serial_number'],
                    'operator': row['operator'],
                    'owner_name': row['owner_name'],
                    'source': row['source']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"[Aircraft DB] Error looking up ICAO24 {icao24}: {e}")
            return None
    
    def lookup_by_registration(self, registration: str) -> Optional[Dict]:
        """Look up aircraft by registration number.
        
        Args:
            registration: Aircraft registration (e.g., 'N12345')
            
        Returns:
            Dictionary with aircraft information or None
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM aircraft WHERE registration = ? COLLATE NOCASE
            ''', (registration.upper(),))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'icao24': row['icao24'],
                    'registration': row['registration'],
                    'manufacturer': row['manufacturer'],
                    'model': row['model'],
                    'type_aircraft': row['type_aircraft'],
                    'serial_number': row['serial_number'],
                    'operator': row['operator'],
                    'owner_name': row['owner_name'],
                    'source': row['source']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"[Aircraft DB] Error looking up registration {registration}: {e}")
            return None
    
    def get_stats(self) -> Dict:
        """Get database statistics.
        
        Returns:
            Dictionary with database stats
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM aircraft')
            total_aircraft = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM aircraft WHERE source = "FAA"')
            faa_count = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM aircraft WHERE source = "OpenSky"')
            opensky_count = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'total_aircraft': total_aircraft,
                'faa_aircraft': faa_count,
                'opensky_aircraft': opensky_count,
                'database_size_mb': self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0,
                'last_update': datetime.fromtimestamp(self.last_update_file.stat().st_mtime) if self.last_update_file.exists() else None
            }
            
        except Exception as e:
            logger.error(f"[Aircraft DB] Error getting stats: {e}")
            return {
                'total_aircraft': 0,
                'faa_aircraft': 0,
                'opensky_aircraft': 0,
                'database_size_mb': 0,
                'last_update': None
            }

