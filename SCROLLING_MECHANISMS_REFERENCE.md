# Scrolling Mechanisms Reference

This document provides a detailed analysis of how the leaderboard, odds ticker, and stock managers implement scrolling functionality. The goal is to identify common patterns that can be extracted into a reusable base class.

---

## Table of Contents
1. [Overview](#overview)
2. [Leaderboard Manager Scrolling](#leaderboard-manager-scrolling)
3. [Odds Ticker Manager Scrolling](#odds-ticker-manager-scrolling)
4. [Stock Manager Scrolling](#stock-manager-scrolling)
5. [Common Patterns](#common-patterns)
6. [Differences & Edge Cases](#differences--edge-cases)
7. [Recommendations for Base Class](#recommendations-for-base-class)

---

## Overview

All three managers implement horizontal scrolling to display content that is wider than the LED matrix display width. They share similar patterns but have some implementation differences.

### Key Similarities
- **Horizontal scrolling**: Content moves from right to left
- **Wide composite image**: Content is pre-rendered into a single wide PIL Image
- **Pixel-based scrolling**: Uses scroll position to crop/display visible portion
- **Configurable speed & delay**: Both scroll speed (pixels per frame) and delay (time between frames) are configurable
- **Dynamic duration**: Can calculate display duration based on content width
- **Loop support**: Can continuously loop content or stop at the end

---

## Leaderboard Manager Scrolling

### Configuration Parameters
```python
# From __init__
self.scroll_speed = self.leaderboard_config.get('scroll_speed', 2)  # pixels per frame
self.scroll_delay = self.leaderboard_config.get('scroll_delay', 0.01)  # seconds between frames
self.loop = self.leaderboard_config.get('loop', True)
self.display_duration = self.leaderboard_config.get('display_duration', 30)

# Dynamic duration
self.dynamic_duration_enabled = self.leaderboard_config.get('dynamic_duration', True)
self.min_duration = self.leaderboard_config.get('min_duration', 30)
self.max_duration = self.leaderboard_config.get('max_duration', 300)
self.duration_buffer = self.leaderboard_config.get('duration_buffer', 0.1)
```

### State Variables
```python
self.scroll_position = 0  # Current horizontal scroll offset in pixels
self.leaderboard_image = None  # The wide composite image containing all content
self.total_scroll_width = 0  # Total width of content for duration calculation
self._display_start_time = 0  # Timestamp when display started
self._end_reached_logged = False  # Flag to prevent logging spam
```

### Image Creation
**Location**: `_create_leaderboard_image()` (lines 898-1136)

**Process**:
1. Calculate total width needed for all leagues and teams
2. Create a single wide PIL Image (`self.leaderboard_image`)
3. Draw each league section horizontally:
   - League logo (64px wide)
   - Teams in horizontal line (logo + number + abbreviation)
   - Spacing between leagues (40px)
4. Store total width in `self.total_scroll_width`

**Key Code**:
```python
# Calculate total width
total_width = 0
spacing = 40  # Spacing between leagues
for league_data in self.leaderboard_data:
    # Calculate league width...
    total_width += league_width + spacing

# Create the composite image
self.leaderboard_image = Image.new('RGB', (total_width, height), (0, 0, 0))

# Draw all content into the image...
```

### Scrolling Logic
**Location**: `display()` method (lines 1309-1472)

**Core Scrolling Algorithm**:
```python
# Lines 1382-1388
# Signal scrolling state
self.display_manager.set_scrolling_state(True)

# Increment scroll position every frame
self.scroll_position += self.scroll_speed

# Add scroll delay
time.sleep(self.scroll_delay)

# Calculate visible crop region
width = self.display_manager.matrix.width
height = self.display_manager.matrix.height
```

**Loop Handling** (lines 1395-1419):
```python
if self.loop:
    # Reset position when we've scrolled past the end for continuous loop
    if self.scroll_position >= self.leaderboard_image.width:
        logger.info(f"Leaderboard loop reset: scroll_position {self.scroll_position} >= image width {self.leaderboard_image.width}")
        self.scroll_position = 0
        logger.info("Leaderboard starting new loop cycle")
else:
    # Stop scrolling when we reach the end
    if self.scroll_position >= self.leaderboard_image.width - width:
        logger.info(f"Leaderboard reached end: scroll_position {self.scroll_position} >= {self.leaderboard_image.width - width}")
        self.scroll_position = self.leaderboard_image.width - width
        # Signal that scrolling has stopped
        self.display_manager.set_scrolling_state(False)
```

**Display Update** (lines 1455-1466):
```python
# Create the visible part by cropping from the composite image
visible_image = self.leaderboard_image.crop((
    self.scroll_position,
    0,
    self.scroll_position + width,
    height
))

# Display the visible portion
self.display_manager.image = visible_image
self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
self.display_manager.update_display()
```

### Dynamic Duration Calculation
**Location**: `calculate_dynamic_duration()` (lines 1138-1231)

**Key Formula**:
```python
# Calculate total scroll distance
if self.loop:
    total_scroll_distance = self.total_scroll_width
else:
    total_scroll_distance = max(0, self.total_scroll_width - display_width)

# Use actual observed scroll speed (from empirical testing)
actual_scroll_speed = 54.2  # pixels per second
total_time = total_scroll_distance / actual_scroll_speed

# Add buffer time
buffer_time = total_time * self.duration_buffer

# For looping: exact cycle time
if self.loop:
    calculated_duration = int(total_time)
else:
    completion_buffer = total_time * 0.05
    calculated_duration = int(total_time + buffer_time + completion_buffer)

# Apply min/max constraints
self.dynamic_duration = max(self.min_duration, min(calculated_duration, self.max_duration))
```

### Special Features
- **FPS Tracking**: Monitors frame rate (lines 1364-1379)
- **Progress Logging**: Throttled logging every 5 seconds (lines 1425-1428)
- **Clean Transitions**: Checks if enough time to complete scroll before duration expires (lines 1431-1453)
- **Force Clear Support**: Can reset scroll position on demand

---

## Odds Ticker Manager Scrolling

### Configuration Parameters
```python
# From __init__
self.scroll_speed = self.odds_ticker_config.get('scroll_speed', 2)  # pixels per frame
self.scroll_delay = self.odds_ticker_config.get('scroll_delay', 0.05)  # seconds between frames
self.loop = self.odds_ticker_config.get('loop', True)
self.display_duration = self.odds_ticker_config.get('display_duration', 30)

# Dynamic duration
self.dynamic_duration_enabled = self.odds_ticker_config.get('dynamic_duration', True)
self.min_duration = self.odds_ticker_config.get('min_duration', 30)
self.max_duration = self.odds_ticker_config.get('max_duration', 300)
self.duration_buffer = self.odds_ticker_config.get('duration_buffer', 0.1)
```

### State Variables
```python
self.scroll_position = 0  # Current horizontal scroll offset
self.ticker_image = None  # The wide composite image
self.total_scroll_width = 0  # Total width for duration calculation
self.last_scroll_time = 0  # Timestamp of last scroll update
self._display_start_time = 0  # When display started
self._end_reached_logged = False  # Prevent logging spam
self._insufficient_time_warning_logged = False  # Additional logging flag
```

### Image Creation
**Location**: `_create_ticker_image()` (not shown in excerpt, but follows similar pattern)

**Process**:
1. Calculate total width for all games
2. Create single wide PIL Image (`self.ticker_image`)
3. Draw each game horizontally with spacing
4. Store total width in `self.total_scroll_width`

### Scrolling Logic
**Location**: `display()` method (lines 1746-1978)

**Core Scrolling Algorithm**:
```python
# Lines 1856-1868
# Check if we should scroll (time-based throttling)
should_scroll = current_time - self.last_scroll_time >= self.scroll_delay

# Signal scrolling state
if should_scroll:
    self.display_manager.set_scrolling_state(True)
    
# Increment scroll position
if should_scroll:
    self.scroll_position += self.scroll_speed
    self.last_scroll_time = current_time
```

**Loop Handling** (lines 1875-1889):
```python
if self.loop:
    # Reset position when scrolled past the end
    if self.scroll_position >= self.ticker_image.width:
        logger.debug(f"Odds ticker loop reset: scroll_position {self.scroll_position} >= image width {self.ticker_image.width}")
        self.scroll_position = 0
else:
    # Stop scrolling at the end
    if self.scroll_position >= self.ticker_image.width - width:
        if not self._end_reached_logged:
            logger.info(f"Odds ticker reached end: scroll_position {self.scroll_position} >= {self.ticker_image.width - width}")
            logger.info("Odds ticker scrolling stopped - reached end of content")
            self._end_reached_logged = True
        self.scroll_position = self.ticker_image.width - width
        self.display_manager.set_scrolling_state(False)
```

**Display Update with Wrap-Around** (lines 1931-1946):
```python
# Create the visible image
visible_image = Image.new('RGB', (width, height))

# Main part - paste with negative offset
visible_image.paste(self.ticker_image, (-self.scroll_position, 0))

# Handle wrap-around for continuous scroll
if self.scroll_position + width > self.ticker_image.width:
    wrap_around_width = (self.scroll_position + width) - self.ticker_image.width
    wrap_around_image = self.ticker_image.crop((0, 0, wrap_around_width, height))
    visible_image.paste(wrap_around_image, (self.ticker_image.width - self.scroll_position, 0))

# Display
self.display_manager.image = visible_image
self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
self.display_manager.update_display()
```

### Dynamic Duration Calculation
**Location**: Similar to leaderboard (uses same formula)

**Key Differences**:
- Uses `self.ticker_image.width` instead of `self.total_scroll_width`
- Same 54.2 px/s actual scroll speed constant

### Special Features
- **Time-based throttling**: Uses `last_scroll_time` to control scroll updates (not every frame)
- **Wrap-around rendering**: Handles seamless looping by pasting content twice
- **Deferred updates**: Can defer data updates while scrolling
- **Thread-safe updates**: Uses threading and queues with timeouts for operations

---

## Stock Manager Scrolling

### Configuration Parameters
```python
# From __init__
self.scroll_speed = self.stocks_config.get('scroll_speed', 1)  # pixels per frame
self.scroll_delay = self.stocks_config.get('scroll_delay', 0.01)  # seconds between frames
self.toggle_chart = self.stocks_config.get('toggle_chart', False)  # Show charts

# Dynamic duration
self.dynamic_duration_enabled = self.stocks_config.get('dynamic_duration', True)
self.min_duration = self.stocks_config.get('min_duration', 30)
self.max_duration = self.stocks_config.get('max_duration', 300)
self.duration_buffer = self.stocks_config.get('duration_buffer', 0.1)
```

### State Variables
```python
self.scroll_position = 0  # Current scroll offset
self.cached_text_image = None  # The wide composite image (note different name)
self.total_scroll_width = 0  # Total width for duration
self.frame_times = []  # For FPS tracking
self.last_frame_time = 0
self.last_fps_log_time = 0
```

### Image Creation
**Location**: `display_stocks()` method (lines 640-708)

**Process**:
```python
# Calculate total width needed
stock_gap = width // 6  # Gap between stocks
element_gap = width // 8  # Gap between elements within stock
total_width = sum(width * 2 for _ in symbols) + stock_gap * (len(symbols) - 1) + element_gap * (len(symbols) * 2 - 1)

# Create the full image
full_image = Image.new('RGB', (total_width, height), (0, 0, 0))

# Draw each stock in sequence
current_x = width  # Initial gap
for symbol in symbols:
    stock_image = self._create_stock_display(symbol, ...)
    full_image.paste(stock_image, (current_x, 0))
    current_x += width * 2 + element_gap
    if symbol != symbols[-1]:
        current_x += stock_gap

# Cache the image
self.cached_text_image = full_image
self.total_scroll_width = total_width
```

### Scrolling Logic
**Location**: `display_stocks()` method (lines 710-751)

**Core Scrolling Algorithm**:
```python
# Line 729 - Continuous circular scrolling
self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width

# Calculate visible portion
visible_portion = self.cached_text_image.crop((
    self.scroll_position, 0,
    self.scroll_position + width, height
))

# Copy to display
self.display_manager.image.paste(visible_portion, (0, 0))
self.display_manager.update_display()

# Add delay
time.sleep(self.scroll_delay)

# Check if completed full cycle
if self.scroll_position == 0:
    return True
```

**Key Difference**: Uses modulo (`%`) for continuous circular scrolling:
```python
self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width
```

This automatically wraps the position back to 0 when it reaches the end.

### Display Update
**Much simpler than others**:
```python
# Lines 732-739
visible_portion = self.cached_text_image.crop((
    self.scroll_position, 0,
    self.scroll_position + width, 
    self.display_manager.matrix.height
))

self.display_manager.image.paste(visible_portion, (0, 0))
self.display_manager.update_display()
```

### Dynamic Duration Calculation
**Location**: `calculate_dynamic_duration()` (lines 753-804)

**Key Formula** (different from leaderboard/odds):
```python
# Calculate total scroll distance
# Text needs to scroll from right edge to completely off left edge
total_scroll_distance = display_width + self.total_scroll_width

# Calculate time based on frames
frames_needed = total_scroll_distance / self.scroll_speed
total_time = frames_needed * self.scroll_delay

# Add buffer
buffer_time = total_time * self.duration_buffer
calculated_duration = int(total_time + buffer_time)

# Apply min/max
self.dynamic_duration = max(self.min_duration, min(calculated_duration, self.max_duration))
```

**Key Difference**: Uses frame-based calculation instead of empirical speed constant.

### Special Features
- **Continuous scrolling**: Always scrolls (no stop option)
- **Modulo-based wrapping**: Simpler loop logic using `%` operator
- **FPS logging**: Detailed frame rate tracking (lines 615-638)
- **Chart toggle**: Can dynamically show/hide charts in ticker
- **Force clear support**: Resets on demand

---

## Common Patterns

### 1. Configuration
All three managers use these common config parameters:
```python
scroll_speed: int           # Pixels to scroll per frame (default: 1-2)
scroll_delay: float         # Seconds between scroll updates (default: 0.01-0.05)
dynamic_duration: bool      # Enable dynamic duration calculation
min_duration: int           # Minimum display duration (default: 30s)
max_duration: int           # Maximum display duration (default: 300s)
duration_buffer: float      # Buffer percentage for duration (default: 0.1 = 10%)
```

### 2. State Variables
```python
scroll_position: int        # Current horizontal offset in pixels
scroll_delay: float         # Time to wait between scroll updates
total_scroll_width: int     # Total width of content for calculations
_display_start_time: float  # Timestamp when display started
```

### 3. Image Management
- All create a **single wide PIL Image** containing all content
- Image is created once and reused (cached)
- Content is drawn horizontally in sequence
- Variable names differ (`leaderboard_image`, `ticker_image`, `cached_text_image`)

### 4. Scroll Update Pattern
```python
# 1. Update scroll position
self.scroll_position += self.scroll_speed

# 2. Handle boundaries (loop or stop)
if self.scroll_position >= content_width:
    if looping:
        self.scroll_position = 0
    else:
        self.scroll_position = content_width - display_width
        
# 3. Extract visible portion
visible_image = content_image.crop((scroll_position, 0, scroll_position + width, height))

# 4. Update display
self.display_manager.image = visible_image
self.display_manager.update_display()

# 5. Add delay
time.sleep(self.scroll_delay)
```

### 5. Dynamic Duration Pattern
```python
def calculate_dynamic_duration(self):
    if not self.dynamic_duration_enabled:
        self.dynamic_duration = fixed_duration
        return
        
    # Calculate scroll distance and time
    total_time = calculate_time_needed(content_width, scroll_speed, scroll_delay)
    buffer_time = total_time * self.duration_buffer
    calculated = int(total_time + buffer_time)
    
    # Apply constraints
    self.dynamic_duration = max(self.min_duration, min(calculated, self.max_duration))
```

### 6. Display Method Structure
```python
def display(self, force_clear: bool = False):
    # 1. Check if enabled
    if not self.is_enabled:
        return
    
    # 2. Reset on force_clear
    if force_clear:
        self.scroll_position = 0
        self._display_start_time = time.time()
    
    # 3. Check for data
    if not self.data:
        self.update()
        if not self.data:
            self._display_fallback_message()
            return
    
    # 4. Create composite image if needed
    if self.composite_image is None:
        self._create_composite_image()
    
    # 5. Signal scrolling state
    self.display_manager.set_scrolling_state(True)
    
    # 6. Update scroll position
    self.scroll_position += self.scroll_speed
    
    # 7. Handle looping/boundaries
    if self.loop:
        if self.scroll_position >= image_width:
            self.scroll_position = 0
    else:
        if self.scroll_position >= image_width - display_width:
            self.scroll_position = image_width - display_width
            self.display_manager.set_scrolling_state(False)
    
    # 8. Extract and display visible portion
    visible = self._extract_visible_portion()
    self.display_manager.image = visible
    self.display_manager.update_display()
    
    # 9. Add delay
    time.sleep(self.scroll_delay)
```

---

## Differences & Edge Cases

### 1. Variable Naming
| Manager | Composite Image | Content Width |
|---------|----------------|---------------|
| Leaderboard | `self.leaderboard_image` | `self.leaderboard_image.width` |
| Odds Ticker | `self.ticker_image` | `self.ticker_image.width` |
| Stock | `self.cached_text_image` | `self.total_scroll_width` |

**Recommendation**: Standardize to `self.composite_image` and always use `self.total_scroll_width`

### 2. Loop Implementation

**Leaderboard & Odds Ticker** (explicit loop flag):
```python
if self.loop:
    if self.scroll_position >= image_width:
        self.scroll_position = 0
else:
    if self.scroll_position >= image_width - display_width:
        self.scroll_position = image_width - display_width
```

**Stock Manager** (always loops using modulo):
```python
self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width
```

**Recommendation**: Support both patterns - configurable loop flag with modulo option

### 3. Scroll Timing

**Leaderboard**: Updates every frame
```python
self.scroll_position += self.scroll_speed
time.sleep(self.scroll_delay)
```

**Odds Ticker**: Time-based throttling
```python
should_scroll = current_time - self.last_scroll_time >= self.scroll_delay
if should_scroll:
    self.scroll_position += self.scroll_speed
    self.last_scroll_time = current_time
```

**Stock Manager**: Updates every frame (like leaderboard)
```python
self.scroll_position = (self.scroll_position + self.scroll_speed) % total_width
time.sleep(self.scroll_delay)
```

**Recommendation**: Support both timing modes (every frame vs time-based)

### 4. Wrap-Around Rendering

**Leaderboard**: Simple crop
```python
visible_image = self.leaderboard_image.crop((
    self.scroll_position, 0,
    self.scroll_position + width, height
))
```

**Odds Ticker**: Explicit wrap-around handling
```python
visible_image = Image.new('RGB', (width, height))
visible_image.paste(self.ticker_image, (-self.scroll_position, 0))

if self.scroll_position + width > self.ticker_image.width:
    wrap_around_width = (self.scroll_position + width) - self.ticker_image.width
    wrap_around_image = self.ticker_image.crop((0, 0, wrap_around_width, height))
    visible_image.paste(wrap_around_image, (self.ticker_image.width - self.scroll_position, 0))
```

**Stock Manager**: Simple crop (relies on modulo)
```python
visible_portion = self.cached_text_image.crop((
    self.scroll_position, 0,
    self.scroll_position + width, height
))
```

**Recommendation**: Implement both crop and wrap-around methods

### 5. Dynamic Duration Calculation

**Leaderboard & Odds Ticker**: Use empirical speed constant
```python
actual_scroll_speed = 54.2  # pixels per second (from log analysis)
total_time = total_scroll_distance / actual_scroll_speed
```

**Stock Manager**: Frame-based calculation
```python
frames_needed = total_scroll_distance / self.scroll_speed
total_time = frames_needed * self.scroll_delay
```

**Recommendation**: Support both calculation methods, with empirical as default

### 6. Display State Signaling

**All three** use `set_scrolling_state()`:
```python
self.display_manager.set_scrolling_state(True)   # When scrolling
self.display_manager.set_scrolling_state(False)  # When stopped
```

**Recommendation**: Keep this pattern in base class

### 7. Clean Transition Logic

**Leaderboard & Odds Ticker** have logic to check if enough time remains:
```python
if remaining_time < 2.0 and self.scroll_position > 0:
    actual_scroll_speed = 54.2
    distance_to_complete = end_position - self.scroll_position
    time_to_complete = distance_to_complete / actual_scroll_speed
    
    if time_to_complete > remaining_time:
        self.scroll_position = 0  # Reset for clean transition
```

**Stock Manager**: No clean transition logic (always continuous)

**Recommendation**: Include in base class as optional feature

---

## Recommendations for Base Class

### Class Structure
```python
class HorizontalScrollManager:
    """Base class for horizontal scrolling content displays."""
    
    def __init__(self, config, display_manager, config_key='scroll'):
        # Configuration
        self.scroll_config = config.get(config_key, {})
        self.scroll_speed = self.scroll_config.get('scroll_speed', 2)
        self.scroll_delay = self.scroll_config.get('scroll_delay', 0.01)
        self.loop = self.scroll_config.get('loop', True)
        self.use_modulo_loop = self.scroll_config.get('use_modulo_loop', False)
        
        # Dynamic duration
        self.dynamic_duration_enabled = self.scroll_config.get('dynamic_duration', True)
        self.min_duration = self.scroll_config.get('min_duration', 30)
        self.max_duration = self.scroll_config.get('max_duration', 300)
        self.duration_buffer = self.scroll_config.get('duration_buffer', 0.1)
        self.dynamic_duration = 60
        
        # State
        self.scroll_position = 0
        self.composite_image = None
        self.total_scroll_width = 0
        self._display_start_time = 0
        self.last_scroll_time = 0
        self._end_reached_logged = False
        
        # Display manager
        self.display_manager = display_manager
        
    # Abstract methods to be implemented by subclasses
    def _create_composite_image(self) -> Image.Image:
        """Create the wide composite image. Must be implemented by subclass."""
        raise NotImplementedError
    
    def _get_content_data(self):
        """Get the content data to display. Must be implemented by subclass."""
        raise NotImplementedError
    
    # Concrete methods provided by base class
    def update_scroll_position(self):
        """Update scroll position based on speed and timing."""
        if self.use_modulo_loop:
            self.scroll_position = (self.scroll_position + self.scroll_speed) % self.total_scroll_width
        else:
            self.scroll_position += self.scroll_speed
    
    def handle_boundaries(self, display_width):
        """Handle scroll boundaries (loop or stop)."""
        if self.use_modulo_loop:
            return  # Already handled by modulo
        
        if self.loop:
            if self.scroll_position >= self.total_scroll_width:
                self.scroll_position = 0
                return True  # Completed loop
        else:
            if self.scroll_position >= self.total_scroll_width - display_width:
                self.scroll_position = self.total_scroll_width - display_width
                self.display_manager.set_scrolling_state(False)
                return True  # Reached end
        return False
    
    def extract_visible_portion(self, display_width, display_height, wrap_around=False):
        """Extract the visible portion of the composite image."""
        if wrap_around and self.scroll_position + display_width > self.total_scroll_width:
            # Create new image and paste with wrap-around
            visible_image = Image.new('RGB', (display_width, display_height))
            visible_image.paste(self.composite_image, (-self.scroll_position, 0))
            
            # Add wrapped content
            wrap_width = (self.scroll_position + display_width) - self.total_scroll_width
            wrap_image = self.composite_image.crop((0, 0, wrap_width, display_height))
            visible_image.paste(wrap_image, (self.total_scroll_width - self.scroll_position, 0))
            
            return visible_image
        else:
            # Simple crop
            return self.composite_image.crop((
                self.scroll_position, 0,
                self.scroll_position + display_width, display_height
            ))
    
    def calculate_dynamic_duration(self, use_empirical=True):
        """Calculate dynamic duration based on content width."""
        if not self.dynamic_duration_enabled:
            self.dynamic_duration = self.scroll_config.get('fixed_duration', 60)
            return
        
        if not self.total_scroll_width:
            self.dynamic_duration = self.min_duration
            return
        
        display_width = self.display_manager.matrix.width
        
        # Calculate scroll distance
        if self.loop:
            total_distance = self.total_scroll_width
        else:
            total_distance = max(0, self.total_scroll_width - display_width)
        
        # Calculate time
        if use_empirical:
            # Use empirical observed speed
            actual_scroll_speed = 54.2  # px/s
            total_time = total_distance / actual_scroll_speed
        else:
            # Use frame-based calculation
            frames_needed = total_distance / self.scroll_speed
            total_time = frames_needed * self.scroll_delay
        
        # Add buffer
        buffer_time = total_time * self.duration_buffer
        calculated = int(total_time + buffer_time)
        
        # Apply constraints
        self.dynamic_duration = max(self.min_duration, min(calculated, self.max_duration))
    
    def should_scroll(self, current_time, use_time_based=False):
        """Determine if scrolling should occur this frame."""
        if use_time_based:
            return current_time - self.last_scroll_time >= self.scroll_delay
        return True  # Scroll every frame
    
    def check_clean_transition(self, current_time):
        """Check if there's enough time to complete scroll before duration expires."""
        elapsed = current_time - self._display_start_time
        remaining = self.dynamic_duration - elapsed
        
        if remaining < 2.0 and self.scroll_position > 0:
            display_width = self.display_manager.matrix.width
            actual_speed = 54.2  # px/s
            
            if self.loop:
                distance = self.total_scroll_width - self.scroll_position
            else:
                end_pos = max(0, self.total_scroll_width - display_width)
                distance = end_pos - self.scroll_position
            
            time_needed = distance / actual_speed
            
            if time_needed > remaining:
                self.scroll_position = 0  # Reset for clean transition
                return True
        return False
    
    def display(self, force_clear=False):
        """Main display method with scrolling logic."""
        # Check enabled state (implemented by subclass)
        if not self.is_enabled:
            return
        
        # Reset on force clear
        if force_clear or not hasattr(self, '_display_start_time'):
            self._display_start_time = time.time()
            self.scroll_position = 0
            self._end_reached_logged = False
        
        # Get data (implemented by subclass)
        data = self._get_content_data()
        if not data:
            self._display_fallback_message()
            return
        
        # Create composite image if needed
        if self.composite_image is None:
            self._create_composite_image()
            if self.composite_image is None:
                self._display_fallback_message()
                return
        
        # Get current time
        current_time = time.time()
        
        # Check if should scroll this frame
        if not self.should_scroll(current_time):
            return
        
        # Signal scrolling state
        self.display_manager.set_scrolling_state(True)
        
        # Update scroll position
        self.update_scroll_position()
        
        # Update last scroll time (for time-based)
        self.last_scroll_time = current_time
        
        # Get display dimensions
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        # Handle boundaries
        completed = self.handle_boundaries(width)
        
        # Check for clean transition
        self.check_clean_transition(current_time)
        
        # Extract visible portion
        visible = self.extract_visible_portion(width, height, wrap_around=self.loop)
        
        # Update display
        self.display_manager.image = visible
        self.display_manager.draw = ImageDraw.Draw(self.display_manager.image)
        self.display_manager.update_display()
        
        # Add delay
        time.sleep(self.scroll_delay)
        
        return completed
```

### Usage Example
```python
class MyCustomScrollManager(HorizontalScrollManager):
    def __init__(self, config, display_manager):
        super().__init__(config, display_manager, config_key='my_scroll')
        self.is_enabled = self.scroll_config.get('enabled', False)
        self.my_data = []
    
    def _get_content_data(self):
        """Return the data to display."""
        return self.my_data
    
    def _create_composite_image(self):
        """Create the wide scrolling image."""
        width = self.display_manager.matrix.width
        height = self.display_manager.matrix.height
        
        # Calculate total width
        total_width = len(self.my_data) * 200
        
        # Create image
        self.composite_image = Image.new('RGB', (total_width, height))
        draw = ImageDraw.Draw(self.composite_image)
        
        # Draw content
        x = 0
        for item in self.my_data:
            # Draw item at x position
            draw.text((x, height // 2), str(item), fill=(255, 255, 255))
            x += 200
        
        # Store width
        self.total_scroll_width = total_width
        
        # Calculate duration
        self.calculate_dynamic_duration()
    
    def _display_fallback_message(self):
        """Show message when no data."""
        # Implement fallback display
        pass
```

### Configuration Example
```json
{
  "my_scroll": {
    "enabled": true,
    "scroll_speed": 2,
    "scroll_delay": 0.01,
    "loop": true,
    "use_modulo_loop": false,
    "dynamic_duration": true,
    "min_duration": 30,
    "max_duration": 300,
    "duration_buffer": 0.1
  }
}
```

---

## Summary

### Core Scrolling Patterns
1. **Wide composite image**: Pre-render all content into single PIL Image
2. **Pixel-based scrolling**: Increment `scroll_position` by `scroll_speed` pixels per frame
3. **Frame timing**: Use `scroll_delay` to control animation speed
4. **Boundary handling**: Either loop back to start or stop at end
5. **Visible extraction**: Crop or paste to show only visible portion
6. **Dynamic duration**: Calculate display time based on content width

### Key Abstractions for Base Class
- `_create_composite_image()`: Build the wide scrolling content
- `_get_content_data()`: Fetch data to display
- `update_scroll_position()`: Handle scroll increment
- `handle_boundaries()`: Loop or stop logic
- `extract_visible_portion()`: Get displayable region
- `calculate_dynamic_duration()`: Determine display time
- `display()`: Main orchestration method

### Configuration Options
- `scroll_speed`: Pixels per frame
- `scroll_delay`: Seconds between frames
- `loop`: Enable/disable looping
- `use_modulo_loop`: Use modulo for simpler continuous scrolling
- `dynamic_duration`: Enable dynamic duration calculation
- `min_duration`, `max_duration`: Duration constraints
- `duration_buffer`: Extra time percentage

This base class would reduce code duplication from ~500 lines per manager to ~100 lines of custom logic per manager.

