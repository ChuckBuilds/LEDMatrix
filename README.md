# LEDMatrix


### Setup video and feature walkthrough on Youtube : 
[![IMAGE ALT TEXT HERE](https://img.youtube.com/vi/_HaqfJy1Y54/0.jpg)](https://www.youtube.com/watch?v=_HaqfJy1Y54)

-----------------------------------------------------------------------------------
### Connect with ChuckBuilds

- Show support on Youtube: https://www.youtube.com/@ChuckBuilds
- Check out the write-up on my website: https://www.chuck-builds.com/led-matrix/
- Stay in touch on Instagram: https://www.instagram.com/ChuckBuilds/
- Want to chat? Reach out on the ChuckBuilds Discord: https://discord.com/invite/uW36dVAtcT
- Feeling Generous? Buy Me a Coffee : https://buymeacoffee.com/chuckbuilds              

-----------------------------------------------------------------------------------

### Special Thanks to:
- Hzeller @ https://github.com/hzeller/rpi-rgb-led-matrix for his groundwork on controlling an LED Matrix from the Raspberry Pi
- Basmilius @ https://github.com/basmilius/weather-icons/ for his free and extensive weather icons
- nvstly @ https://github.com/nvstly/icons for their Stock and Crypto Icons
- ESPN for their sports API
- Yahoo Finance for their Stock API
- OpenWeatherMap for their Free Weather API
- Randomwire @ https://www.thingiverse.com/thing:5169867 for their 4mm Pixel Pitch LED Matrix Stand 




-----------------------------------------------------------------------------------

## Core Features

<details>
<summary>Core Features</summary>
## Core Features
Modular, rotating Displays that can be individually enabled or disabled per the user's needs with some configuration around display durations, teams, stocks, weather, timezones, and more. Displays include:

### Time and Weather
- Real-time clock display (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01361](https://github.com/user-attachments/assets/c4487d40-5872-45f5-a553-debf8cea17e9)


- Current Weather, Daily Weather, and Hourly Weather Forecasts (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01362](https://github.com/user-attachments/assets/d31df736-522f-4f61-9451-29151d69f164)
![DSC01364](https://github.com/user-attachments/assets/eb2d16ad-6b12-49d9-ba41-e39a6a106682)
![DSC01365](https://github.com/user-attachments/assets/f8a23426-e6fa-4774-8c87-19bb94cfbe73)


- Google Calendar event display (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01374-1](https://github.com/user-attachments/assets/5bc89917-876e-489d-b944-4d60274266e3)



### Sports Information
The system supports live, recent, and upcoming game information for multiple sports leagues:
- NHL (Hockey) (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01356](https://github.com/user-attachments/assets/64c359b6-4b99-4dee-aca0-b74debda30e0)
![DSC01339](https://github.com/user-attachments/assets/2ccc52af-b4ed-4c06-a341-581506c02153)
![DSC01337](https://github.com/user-attachments/assets/f4faf678-9f43-4d37-be56-89ecbd09acf6)

- NBA (Basketball)
- MLB (Baseball) (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01359](https://github.com/user-attachments/assets/71e985f1-d2c9-4f0e-8ea1-13eaefeec01c)

- NFL (Football) (2x 96x48 Displays 2.5mm Pixel Pitch)
  <img width="2109" height="541" alt="image" src="https://github.com/user-attachments/assets/d10212c9-0d45-4f87-b61d-0a33afb9f160" />
- NCAA Football (2x 96x48 Displays 2.5mm Pixel Pitch)
  <img width="2417" height="610" alt="image" src="https://github.com/user-attachments/assets/9be92869-ee29-4809-9337-69977f228e23" />

- NCAA Men's Basketball
- NCAA Men's Baseball
- Soccer (Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Liga Portugal, Champions League, Europa League, MLS)
- (Note, some of these sports seasons were not active during development and might need fine tuning when games are active)


### Financial Information
- Near real-time stock & crypto price updates
- Stock news headlines
- Customizable stock & crypto watchlists (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01366](https://github.com/user-attachments/assets/95b67f50-0f69-4479-89d0-1d87c3daefd3)
![DSC01368](https://github.com/user-attachments/assets/c4b75546-388b-4d4a-8b8c-8c5a62f139f9)



### Entertainment
- Music playback information from multiple sources:
  - Spotify integration
  - YouTube Music integration
- Album art display
- Now playing information with scrolling text (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01354](https://github.com/user-attachments/assets/7524b149-f55d-4eb7-b6c6-6e336e0d1ac1)
![DSC01389](https://github.com/user-attachments/assets/3f768651-5446-4ff5-9357-129cd8b3900d)



### Custom Display Features
- Custom Text display (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01379](https://github.com/user-attachments/assets/338b7578-9d4b-4465-851c-7e6a1d999e07)

- Youtube Subscriber Count Display (2x 64x32 Displays 4mm Pixel Pitch)
![DSC01376](https://github.com/user-attachments/assets/7ea5f42d-afce-422f-aa97-6b2a179aa7d2)

- Font testing Display (not in rotation)
</details>

-----------------------------------------------------------------------------------

## Hardware

<details>
<summary>Hardware Requirements</summary>
## Hardware Requirements

### Raspberry Pi
- **Raspberry Pi 3B or 4 (NOT RPi 5!)**  
  [Amazon Affiliate Link – Raspberry Pi 4 4GB](https://amzn.to/4dJixuX)

### RGB Matrix Bonnet / HAT
- [Adafruit RGB Matrix Bonnet/HAT](https://www.adafruit.com/product/3211) – supports one “chain” of horizontally connected displays  
- [Adafruit Triple LED Matrix Bonnet](https://www.adafruit.com/product/6358) – supports up to 3 vertical “chains” of horizontally connected displays *(use `regular-pi1` as hardware mapping)*  
- [Electrodragon RGB HAT](https://www.electrodragon.com/product/rgb-matrix-panel-drive-board-raspberry-pi/) – supports up to 3 vertical “chains”  
- [Seengreat Matrix Adapter Board](https://amzn.to/3KsnT3j) – single-chain LED Matrix *(use `regular` as hardware mapping)*  

### LED Matrix Panels  
(2x in a chain recommended)
- [Adafruit 64×32](https://www.adafruit.com/product/2278) – designed for 128×32 but works with dynamic scaling on many displays (pixel pitch is user preference)
- [Waveshare 64×32](https://amzn.to/3Kw55jK) - Does not require E addressable pad
- [Waveshare 92×46](https://amzn.to/4pQdezE) – higher resolution, requires soldering the **E addressable pad** on the [Adafruit RGB Bonnet](https://www.adafruit.com/product/3211) to “8” **OR** toggling the DIP switch on the Adafruit Triple LED Matrix Bonnet *(no soldering required!)*  
  > Amazon Affiliate Link – ChuckBuilds receives a small commission on purchases  

### Power Supply
- [5V 4A DC Power Supply](https://www.adafruit.com/product/658) (good for 2 -3 displays, depending on brightness and pixel density, you'll need higher amperage for more)
- [5V 10AM DC Power Supply](https://amzn.to/3IKlYqe) (good for 6-8 displays, depending on brightness and pixel density)

## Optional but recommended mod for Adafruit RGB Matrix Bonnet
- By soldering a jumper between pins 4 and 18, you can run a specialized command for polling the matrix display. This provides better brightness, less flicker, and better color.
- If you do the mod, we will use the default config with led-gpio-mapping=adafruit-hat-pwm, otherwise just adjust your mapping in config.json to adafruit-hat
- More information available: https://github.com/hzeller/rpi-rgb-led-matrix/tree/master?tab=readme-ov-file
![DSC00079](https://github.com/user-attachments/assets/4282d07d-dfa2-4546-8422-ff1f3a9c0703)

## Possibly required depending on the display you are using.
- Some LED Matrix displays require an "E" addressable line to draw the display properly. The [64x32 Adafruit display](https://www.adafruit.com/product/2278) does NOT require the E addressable line, however the [92x46 Waveshare display](https://amzn.to/4pQdezE) DOES require the "E" Addressable line.
- Various ways to enable this depending on your Bonnet / HAT.

Your display will look like it is "sort of" working but still messed up. 
<img width="841" height="355" alt="image" src="https://github.com/user-attachments/assets/7b8cfa98-270c-4c41-9cdc-146535eec32f" />
or 
<img width="924" height="316" alt="image" src="https://github.com/user-attachments/assets/fda59057-faca-401b-8d55-f0e360cadbdf" />
or
<img width="1363" height="703" alt="image" src="https://github.com/user-attachments/assets/0e833721-1690-4446-a6a9-7c48eed7a633" />

How to set addressable E line on various HATs:

- Adafruit Single Chain HATs
<img width="719" height="958" alt="IMG_5228" src="https://github.com/user-attachments/assets/b30e839c-6fc9-4129-a99c-0f4eaf62c89d" />
or
<img width="349" height="302" alt="image" src="https://github.com/user-attachments/assets/2175fa40-98a8-4da7-bcd3-d6b1714e33d2" />

- Adafruit Triple Chain HAT
  ![6358-06](https://github.com/user-attachments/assets/f9570fe5-25c6-4340-811a-a3f0d71559a0)

- ElectroDragon RGB LED Matrix Panel Drive Board
![RGB-Matrix-Panel-Drive-Board-For-Raspberry-Pi-02-768x574](https://github.com/user-attachments/assets/6cfe2545-0fc4-49d6-a314-dfdb229258c6)





2 Matrix display with Rpi connected to Adafruit Single Chain HAT.
![DSC00073](https://github.com/user-attachments/assets/a0e167ae-37c6-4db9-b9ce-a2b957ca1a67)


</details>

<details>

<summary>Mount / Stand options</summary>


## Mount/Stand
I 3D printed stands to keep the panels upright and snug.

Thanks to "Randomwire" for making these for the 4mm Pixel Pitch LED Matrix : https://www.thingiverse.com/thing:5169867

Thanks for Rmatze for making:
- 3mm Pixel Pitch RGB Stand for 32x64 Display : https://www.thingiverse.com/thing:7149818 
- 4mm Pixel Pitch RGB Stand for 32x64 Display : https://www.thingiverse.com/thing:7165993

These are not required and you can probably rig up something basic with stuff you have around the house. I used these screws: https://amzn.to/4mFwNJp (Amazon Affiliate Link)

</details>

-----------------------------------------------------------------------------------
## Installation Steps


<details>

<summary>Preparing the Raspberry Pi</summary>

# Preparing the Raspberry Pi
1. Create RPI Image on a Micro-SD card (I use 16gb because I have it, size is not too important but I would use 8gb or more) using [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose your Raspberry Pi (3B+ in my case) 
3. For Operating System (OS), choose "Other", then choose Raspbian OS (64-bit) Lite. Both **Debian 12 (Bookworm)** and **Debian 13 (Trixie)** are fully supported. For Trixie-specific installation guidance, see [Trixie Migration Guide](docs/TRIXIE_MIGRATION_GUIDE.md).
4. For Storage, choose your micro-sd card
![image](https://github.com/user-attachments/assets/05580e0a-86d5-4613-aadc-93207365c38f)
5. Press Next then Edit Settings
![image](https://github.com/user-attachments/assets/b392a2c9-6bf4-47d5-84b7-63a5f793a1df)
6. Inside the OS Customization Settings, choose a name for your device. I use "ledpi". Choose a password, enter your WiFi information, and set your timezone.
![image](https://github.com/user-attachments/assets/0c250e3e-ab3c-4f3c-ba60-6884121ab176)
7. Under the Services Tab, make sure that SSH is enabled. I recommend using password authentication for ease of use - it is the password you just chose above.
![image](https://github.com/user-attachments/assets/1d78d872-7bb1-466e-afb6-0ca26288673b)
8. Then Click "Save" and Agree to Overwrite the Micro-SD card.
</details>



<details>

<summary>System Setup & Installation</summary>

# System Setup & Installation

1. Open PowerShell and ssh into your Raspberry Pi with ledpi@ledpi (or Username@Hostname)
```bash
ssh ledpi@ledpi
```

2. Update repositories, upgrade raspberry pi OS, install git
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip cython3 build-essential python3-dev python3-pillow scons
```

3. Clone this repository:
```bash
git clone https://github.com/ChuckBuilds/LEDMatrix.git
cd LEDMatrix
```


4. First-time installation (recommended)

```bash
chmod +x first_time_install.sh
sudo ./first_time_install.sh
```

This single script installs services, dependencies, configures permissions and sudoers, and validates the setup.

</details>

<details>

<summary>Outdated Installation Steps left for reference</summary>


-----------------------------------------------------------------------------------
----- OLD STEPS (left for manual review, you don't need to run these if you run the First Time Install Script) -----
4. Install dependencies:
```bash
sudo pip3 install --break-system-packages -r requirements.txt
```
--break-system-packages allows us to install without a virtual environment


5. Install rpi-rgb-led-matrix dependencies:
```bash
cd rpi-rgb-led-matrix-master
```
```bash
sudo make build-python PYTHON=$(which python3)
```
```bash
cd bindings/python
sudo python3 setup.py install
```
Test it with:
```bash
python3 -c 'from rgbmatrix import RGBMatrix, RGBMatrixOptions; print("Success!")'
```

## Important: Sound Module Configuration

1. Remove unnecessary services that might interfere with the LED matrix:
```bash
sudo apt-get remove bluez bluez-firmware pi-bluetooth triggerhappy pigpio
```

2. Blacklist the sound module:
```bash
cat <<EOF | sudo tee /etc/modprobe.d/blacklist-rgb-matrix.conf
blacklist snd_bcm2835
EOF
```

then execute

```bash
sudo update-initramfs -u
```

3. Reboot:
```bash
sudo reboot
```

## Performance Optimization

To reduce flickering and improve display quality:

1. Edit `/boot/firmware/cmdline.txt`:
```bash
sudo nano /boot/firmware/cmdline.txt
```

2. Add `isolcpus=3` at the end of the line

3. Ctrl + X to exit, Y to save, Enter to Confirm

4. Edit /boot/firmware/config.txt  with
```bash
sudo nano /boot/firmware/config.txt
```  

6. Edit the `dtparam=audio=on` section to `dtparam=audio=off`

7. Ctrl + X to exit, Y to save, Enter to Confirm

8. Save and reboot:
```bash
sudo reboot
```
## Configuration

### Initial Setup

1. **First-time setup**: (First Time Script should do this for you) Copy the template to create your config:
   ```bash
   cp config/config.template.json config/config.json
   ```

</details>



## Configuration

<details>

<summary>Configuration</summary>

## Configuration

### Initial Setup

The system uses a template-based configuration approach to avoid Git conflicts during updates:

1. **First-time setup**: The previous "First_time_install.sh" script should've already copied the template to create your config.json:

2. **Edit your configuration**: 
   ```bash
   sudo nano config/config.json
   ```
or edit via web interface at http://ledpi:5001

3. **Having Issues?**: Run the First Time Script again:
  ```bash
  sudo ./first_time_install.sh
  ```


### API Keys and Secrets

For sensitive settings like API keys:
1. Copy the secrets template: `cp config/config_secrets.template.json config/config_secrets.json`
2. Edit `config/config_secrets.json` with your API keys via `sudo nano config/config_secrets.json`
3. Ctrl + X to exit, Y to overwrite, Enter to Confirm

### Automatic Configuration Migration

The system automatically handles configuration updates:
- **New installations**: Creates `config.json` from the template automatically
- **Existing installations**: Automatically adds new configuration options with default values when the system starts
- **Backup protection**: Creates a backup of your current config before applying updates
- **No conflicts**: Your custom settings are preserved while new options are added

Everything is configured via `config/config.json` and `config/config_secrets.json`. The `config.json` file is not tracked by Git to prevent conflicts during updates.

</details>


<details>

<summary>Calendar Display Configuration</summary>


## Calendar Display Configuration

The calendar display module shows upcoming events from your Google Calendar. To configure it:

1. In `config/config.json`, add the following section:
```json
{
    "calendar": {
        "enabled": true,
        "update_interval": 300,  // Update interval in seconds (default: 300)
        "max_events": 3,         // Maximum number of events to display
        "calendars": ["primary"] // List of calendar IDs to display
    }
}
```

2. Set up Google Calendar API access:
   1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
   2. Create a new project or select an existing one
   3. Enable the Google Calendar API
   4. Create OAuth 2.0 credentials:
      - Application type: TV and Limited Input Device
      - Download the credentials file as `credentials.json`
   5. Place the `credentials.json` file in your project root directory

3. On first run, the application will:
   - Provide a code to enter at https://www.google.com/device for Google authentication
   - Request calendar read-only access
   - Save the authentication token as `token.pickle`

The calendar display will show:
- Event date and time
- Event title (wrapped to fit the display)
- Up to 3 upcoming events (configurable)
</details>

<details>

<summary>Odds Ticker Configuration</summary>

## Odds Ticker Configuration

The odds ticker displays betting odds for upcoming sports games. To configure it:

1. In `config/config.json`, add the following section:
```json
{
    "odds-ticker": {
        "enabled": true,
        "enabled_leagues": ["nfl", "nba", "mlb", "ncaa_fb"],
        "update_interval": 3600,
        "scroll_speed": 2,
        "scroll_delay": 0.05,
        "display_duration": 30
    }
}
```

### Configuration Options

- **`enabled`**: Enable/disable the odds ticker (default: false)
- **`enabled_leagues`**: Array of leagues to display (options: "nfl", "nba", "mlb", "ncaa_fb")
- **`update_interval`**: How often to fetch new odds data in seconds (default: 3600)
- **`scroll_speed`**: Pixels to scroll per update (default: 1)
- **`scroll_delay`**: Delay between scroll updates in seconds (default: 0.05)
- **`display_duration`**: How long to show each game in seconds (default: 30)

**How it works:**
- The ticker intelligently filters games based on the `"show_favorite_teams_only"` setting within each individual sport's configuration block (e.g., `"nfl_scoreboard"`). If set to `true` for a sport, only favorite teams from that sport will appear in the ticker.
- Games are sorted by the soonest start time.

### Display Format

The odds ticker shows information in this format:
```
[12:00 PM] DAL -6.5 ML -200 O/U 47.5 vs NYG ML +175
```

Where:
- `[12:00 PM]` - Game time in local timezone
- `DAL` - Away team abbreviation
- `-6.5` - Spread for away team (negative = favored)
- `ML -200` - Money line for away team
- `O/U 47.5` - Over/under total
- `vs` - Separator
- `NYG` - Home team abbreviation
- `ML +175` - Money line for home team

### Team Logos

The ticker displays team logos alongside the text:
- Away team logo appears to the left of the text
- Home team logo appears to the right of the text
- Logos are automatically resized to fit the display

### Requirements

- ESPN API access for odds data
- Team logo files in the appropriate directories:
  - `assets/sports/nfl_logos/`
  - `assets/sports/nba_logos/`
  - `assets/sports/mlb_logos/`
  - `assets/sports/ncaa_logos/`

### Troubleshooting

**No Games Displayed:**
1. **League Configuration**: Ensure the leagues you want are enabled in their respective config sections
2. **Favorite Teams**: If `show_favorite_teams_only` is true, ensure you have favorite teams configured
3. **API Access**: Verify ESPN API is accessible and returning data
4. **Time Window**: The ticker only shows games in the next 7 days

**No Odds Data:**
1. **API Timing**: Odds may not be available immediately when games are scheduled
2. **League Support**: Not all leagues may have odds data available
3. **API Limits**: ESPN API may have rate limits or temporary issues

**Performance Issues:**
1. **Reduce scroll_speed**: Try setting it to 1 instead of 2
2. **Increase scroll_delay**: Try 0.1 instead of 0.05
3. **Check system resources**: Ensure the Raspberry Pi has adequate resources

### Testing

You can test the odds ticker functionality using:
```bash
python test_odds_ticker.py
```

This will:
1. Initialize the odds ticker
2. Fetch upcoming games and odds
3. Display sample games
4. Test the scrolling functionality
</details>


<details>

<summary>Stocks Configuration</summary>

## Stocks Configuration

The stocks display shows real-time stock and crypto prices in a scrolling ticker format. To configure it:

1. In `config/config.json`, add the following section:
```json
{
    "stocks": {
        "enabled": true,
        "symbols": ["AAPL", "MSFT", "GOOGL", "TSLA"],
        "update_interval": 600,
        "scroll_speed": 1,
        "scroll_delay": 0.01,
        "toggle_chart": false
    }
}
```

### Configuration Options

- **`enabled`**: Enable/disable the stocks display (default: false)
- **`symbols`**: Array of stock symbols to display (e.g., ["AAPL", "MSFT", "GOOGL"])
- **`update_interval`**: How often to fetch new stock data in seconds (default: 600)
- **`scroll_speed`**: Pixels to scroll per update (default: 1)
- **`scroll_delay`**: Delay between scroll updates in seconds (default: 0.01)
- **`toggle_chart`**: Enable/disable mini charts in the scrolling ticker (default: false)

### Display Format

The stocks display shows information in this format:
```
[Logo] SYMBOL
       $PRICE
       +CHANGE (+PERCENT%)
```

Where:
- `[Logo]` - Stock/crypto logo (if available)
- `SYMBOL` - Stock symbol (e.g., AAPL, MSFT)
- `$PRICE` - Current stock price
- `+CHANGE` - Price change (green for positive, red for negative)
- `+PERCENT%` - Percentage change

### Chart Toggle Feature

The `toggle_chart` setting controls whether mini price charts are displayed alongside each stock:

- **`"toggle_chart": true`**: Shows mini line charts on the right side of each stock display
- **`"toggle_chart": false`**: Shows only text information (symbol, price, change)

When charts are disabled, the text is centered more prominently on the display.

### Crypto Support

The system also supports cryptocurrency symbols. Add crypto symbols to the `symbols` array:

```json
{
    "stocks": {
        "enabled": true,
        "symbols": ["AAPL", "MSFT", "BTC-USD", "ETH-USD"],
        "update_interval": 600,
        "scroll_speed": 1,
        "scroll_delay": 0.01,
        "toggle_chart": false
    }
}
```

### Requirements

- Yahoo Finance API access for stock data
- Stock/crypto logo files in the appropriate directories:
  - `assets/stocks/ticker_icons/` (for stocks)
  - `assets/stocks/crypto_icons/` (for cryptocurrencies)

### Troubleshooting

**No Stock Data Displayed:**
1. **Symbol Format**: Ensure stock symbols are correct (e.g., "AAPL" not "apple")
2. **API Access**: Verify Yahoo Finance API is accessible
3. **Market Hours**: Some data may be limited during off-hours
4. **Symbol Validity**: Check that symbols exist and are actively traded

**Performance Issues:**
1. **Reduce scroll_speed**: Try setting it to 1 instead of higher values
2. **Increase scroll_delay**: Try 0.05 instead of 0.01 for smoother scrolling
3. **Reduce symbols**: Limit the number of symbols to improve performance

### Testing

You can test the stocks functionality using:
```bash
python test/test_stock_toggle_chart.py
```

This will:
1. Test the toggle_chart functionality
2. Verify configuration loading
3. Test cache clearing behavior

</details>

<details>

<summary>Football Configuration</summary>


## Football Game-Based Configuration (NFL & NCAA FB)

For NFL and NCAA Football, the system now uses a game-based fetch approach instead of time-based windows. This is more practical for football since games are weekly and you want to show specific numbers of games rather than arbitrary time periods.

### Configuration Options

Instead of using `past_fetch_days` and `future_fetch_days`, the system now uses:

- **`fetch_past_games`**: Number of recent games to fetch (default: 1)
- **`fetch_future_games`**: Number of upcoming games to fetch (default: 1)

### Example Configuration

```json
{
    "nfl_scoreboard": {
        "enabled": true,
        "fetch_past_games": 1,
        "fetch_future_games": 1,
        "favorite_teams": ["TB", "DAL"]
    },
    "ncaa_fb_scoreboard": {
        "enabled": true,
        "fetch_past_games": 1,
        "fetch_future_games": 1,
        "favorite_teams": ["UGA", "AUB"]
    }
}
```

### How It Works

- **`fetch_past_games: 1`**: Shows the most recent game for your favorite teams
- **`fetch_future_games: 1`**: Shows the next upcoming game for your favorite teams
- **`fetch_future_games: 2`**: Shows the next two upcoming games (e.g., Week 1 and Week 2 matchups)

### Benefits

1. **Predictable Results**: Always shows exactly the number of games you specify
2. **Season Flexibility**: Works well both during the season and in the off-season
3. **Future Planning**: Can show games far in the future (e.g., Week 1 when it's 40 days away)
4. **Efficient**: Only fetches the games you actually want to see

### Use Cases

- **During Season**: `fetch_future_games: 1` shows next week's game
- **Off-Season**: `fetch_future_games: 1` shows the first scheduled game (even if it's months away)
- **Planning**: `fetch_future_games: 2` shows the next two matchups for planning purposes

</details>


<details>

<summary> Music Display Configuration </summary>

## Music Display Configuration

The Music Display module shows information about the currently playing track from either Spotify or YouTube Music (via the [YouTube Music Desktop App](https://ytmdesktop.app/) companion server).

**Setup Requirements:**

1.  **Spotify:**
    *   Requires a Spotify account (for API access).
    *   You need to register an application on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/) to get API credentials.
        *   Go to the dashboard, log in, and click "Create App".
        *   Give it a name (e.g., "LEDMatrix Display") and description.
        *   For the "Redirect URI", enter `http://127.0.0.1:8888/callback` (or another unused port if 8888 is taken). You **must** add this exact URI in your app settings on the Spotify dashboard.
        *   Note down the `Client ID` and `Client Secret`.

2.  **YouTube Music (YTM):**
    *   Requires the [YouTube Music Desktop App](https://ytmdesktop.app/) (YTMD) to be installed and running on a computer on the *same network* as the Raspberry Pi.
    *   In YTMD settings, enable the "Companion Server" under Integration options. Note the URL it provides (usually `http://localhost:9863` if running on the same machine, or `http://<YTMD-Computer-IP>:9863` if running on a different computer).

**`preferred_source` Options:**
*   `"spotify"`: Only uses Spotify. Ignores YTM.
*   `"ytm"`: Only uses the YTM Companion Server. Ignores Spotify.

### Spotify Authentication for Music Display

If you are using the Spotify integration to display currently playing music, you will need to authenticate with Spotify. This project uses an authentication flow that requires a one-time setup. Due to how the display controller script may run with specific user permissions (even when using `sudo`), the following steps are crucial:

1.  **Initial Setup & Secrets:**
    *   Ensure you have your Spotify API Client ID, Client Secret, and Redirect URI.
    *   The Redirect URI should be set to `http://127.0.0.1:8888/callback` in your Spotify Developer Dashboard.
    *   Copy `config/config_secrets.template.json` to `config/config_secrets.json`.
    *   Edit `config/config_secrets.json` and fill in your Spotify credentials under the `"music"` section:
        ```json
        {
          "music": {
            "SPOTIFY_CLIENT_ID": "YOUR_SPOTIFY_CLIENT_ID",
            "SPOTIFY_CLIENT_SECRET": "YOUR_SPOTIFY_CLIENT_SECRET",
            "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback"
          }
        }
        ```

2.  **Run the Authentication Script:**
    *   Execute the authentication script using `sudo`. This is important because it needs to create an authentication cache file (`spotify_auth.json`) that will be owned by root.
        ```bash
        sudo python3 src/authenticate_spotify.py
        ```
    *   The script will output a URL. Copy this URL and paste it into a web browser on any device.
    *   Log in to Spotify and authorize the application.
    *   Your browser will be redirected to a URL starting with `http://127.0.0.1:8888/callback?code=...`. It will likely show an error page like "This site can't be reached" – this is expected.
    *   Copy the **entire** redirected URL from your browser's address bar.
    *   Paste this full URL back into the terminal when prompted by the script.
    *   If successful, it will indicate that token info has been cached.

3.  **Adjust Cache File Permissions:**
    *   The main display script (`display_controller.py`), even when run with `sudo`, might operate with an effective User ID (e.g., UID 1 for 'daemon') that doesn't have permission to read the `spotify_auth.json` file created by `root` (which has -rw------- permissions by default).
    *   To allow the display script to read this cache file, change its permissions:
        ```bash
        sudo chmod 644 config/spotify_auth.json
        ```
    This makes the file readable by all users, including the effective user of the display script.

4.  **Run the Main Application:**
    *   You should now be able to run your main display controller script using `sudo`:
        ```bash
        sudo python3 display_controller.py
        ```
    *   The Spotify client should now authenticate successfully using the cached token.

**Why these specific permissions steps?**

The `authenticate_spotify.py` script, when run with `sudo`, creates `config/spotify_auth.json` owned by `root`. If the main `display_controller.py` (also run with `sudo`) effectively runs as a different user (e.g., UID 1/daemon, as observed during troubleshooting), that user won't be able to read the `root`-owned file unless its permissions are relaxed (e.g., to `644`). The `chmod 644` command allows the owner (`root`) to read/write, and everyone else (including the `daemon` user) to read.

### Youtube Music Authentication for Music Display

The system can display currently playing music information from [YouTube Music Desktop (YTMD)](https://ytmdesktop.app/) via its Companion server API.

### YouTube Display Configuration & API Key

The YouTube display module shows channel statistics for a specified YouTube channel. To configure it:

1. In `config/config.json`, add the following section:
```json
{
    "youtube": {
        "enabled": true,
        "update_interval": 300  // Update interval in seconds (default: 300)
    }
}
```

2. In `config/config_secrets.json`, add your YouTube API credentials:
```json
{
    "youtube": {
        "api_key": "YOUR_YOUTUBE_API_KEY",
        "channel_id": "YOUR_CHANNEL_ID"
    }
}
```

To get these credentials:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the YouTube Data API v3
4. Create credentials (API key)
5. For the channel ID, you can find it in your YouTube channel URL or use the YouTube Data API to look it up

**Setup:**

1.  **Enable Companion Server in YTMD:**
    *   In the YouTube Music Desktop application, go to `Settings` -> `Integrations`.
    *   Enable the "Companion Server".
    *   Note the IP address and Port it's listening on (default is usually `http://localhost:9863`), you'll need to know the local ip address if playing music on a device other than your rpi (probably are).

2.  **Configure `config/config.json`:**
    *   Update the `music` section in your `config/config.json`:
        ```json
        "music": {
            "enabled": true,
            "preferred_source": "ytm",
            "YTM_COMPANION_URL": "http://YOUR_YTMD_IP_ADDRESS:PORT", // e.g., "http://localhost:9863" or "http://192.168.1.100:9863"
            "POLLING_INTERVAL_SECONDS": 1
        }
        ```

3.  **Initial Authentication & Token Storage:**
    *   The first time you run ` python3 src/authenticate_ytm.py` after enabling YTM, it will attempt to register itself with the YTMD Companion Server.
    *   You will see log messages in the terminal prompting you to **approve the "LEDMatrixController" application within the YouTube Music Desktop app.** You typically have 30 seconds to do this.
    *   Once approved, an authentication token is saved to your `config/ytm_auth.json`.
    *   This ensures the `ledpi` user owns the config directory and file, and has the necessary write permissions.

**Troubleshooting:**
*   "No authorized companions" in YTMD: Ensure you've approved the `LEDMatrixController` in YTMD settings after the first run.
*   Connection errors: Double-check the `YTM_COMPANION_URL` in `config.json` matches what YTMD's companion server is set to.
*   Ensure your firewall (Windows Firewall) allows YTM Desktop app to access local networks.

</details>


------------------------------------------------------------------------------------
## Before Running the Display
- To allow the script to properly access fonts, you need to set the correct permissions on your home directory:
  ```bash
  sudo chmod o+x /home/ledpi
  ```
- Replace ledpi with your actual username, if different.
You can confirm your username by executing:
`whoami`


## Running the Display

From the project root directory:
```bash
sudo python3 display_controller.py
```
This will start the display cycle but only stays active as long as your ssh session is active.


-----------------------------------------------------------------------------------

<details>

<summary>Run on Startup Automatically with Systemd Service Installation</summary>


## Run on Startup Automatically with Systemd Service Installation

The LEDMatrix can be installed as a systemd service to run automatically at boot and be managed easily. The service runs as root to ensure proper hardware timing access for the LED matrix.

### Installing the Service (this is included in the first_time_install.sh)

1. Make the install script executable:
```bash
chmod +x install_service.sh
```

2. Run the install script with sudo:
```bash
sudo ./install_service.sh
```

The script will:
- Detect your user account and home directory
- Install the service file with the correct paths
- Enable the service to start on boot
- Start the service immediately

### Managing the Service

The following commands are available to manage the service:

```bash
# Stop the display
sudo systemctl stop ledmatrix.service

# Start the display
sudo systemctl start ledmatrix.service

# Check service status
sudo systemctl status ledmatrix.service

# View logs
journalctl -u ledmatrix.service

# Disable autostart
sudo systemctl disable ledmatrix.service

# Enable autostart
sudo systemctl enable ledmatrix.service
```
</details>

<details>

<summary>Convenience Scripts</summary>


### Convenience Scripts

Two convenience scripts are provided for easy service management:

- `start_display.sh` - Starts the LED matrix display service
- `stop_display.sh` - Stops the LED matrix display service

Make them executable with:
```bash
chmod +x start_display.sh stop_display.sh
```

Then use them to control the service:
```bash
sudo ./start_display.sh
sudo ./stop_display.sh
```
</details>
-----------------------------------------------------------------------------------

## Web Interface Installation (V2)

The LEDMatrix system includes Web Interface V2 that runs on port 5001 and provides real-time display preview, configuration management, and on-demand display controls.

### Installing the Web Interface Service

1. Make the install script executable:
```bash
chmod +x install_web_service.sh
```

2. Run the install script with sudo:
```bash
sudo ./install_web_service.sh
```

The script will:
- Copy the web service file to `/etc/systemd/system/`
- Enable the service to start on boot
- Start the service immediately
- Show the service status

### Web Interface Configuration

The web interface can be configured to start automatically with the main display service:

1. In `config/config.json`, ensure the web interface autostart is enabled:
```json
{
    "web_display_autostart": true
}
```

2. The web interface will now start automatically when:
   - The system boots
   - The `web_display_autostart` setting is `true` in your config

### Accessing the Web Interface

Once installed, you can access the web interface at:
```
http://your-pi-ip:5001
```

### Managing the Web Interface Service

```bash
# Check service status
sudo systemctl status ledmatrix-web.service

# View logs
journalctl -u ledmatrix-web.service -f

# Stop the service
sudo systemctl stop ledmatrix-web.service

# Start the service
sudo systemctl start ledmatrix-web.service

# Disable autostart
sudo systemctl disable ledmatrix-web.service

# Enable autostart
sudo systemctl enable ledmatrix-web.service
```

### Web Interface Features

- **Real-time Display Preview**: See what's currently displayed on the LED matrix
- **Configuration Management**: Edit settings through a web interface
- **On-Demand Controls**: Start specific displays (weather, stocks, sports) on demand
- **Service Management**: Start/stop the main display service
- **System Controls**: Restart, update code, and manage the system
- **API Metrics**: Monitor API usage and system performance
- **Logs**: View system logs in real-time

### Troubleshooting Web Interface

**Web Interface Not Accessible After Restart:**
1. Check if the web service is running: `sudo systemctl status ledmatrix-web.service`
2. Verify the service is enabled: `sudo systemctl is-enabled ledmatrix-web.service`
3. Check logs for errors: `journalctl -u ledmatrix-web.service -f`
4. Ensure `web_display_autostart` is set to `true` in `config/config.json`

**Port 5001 Not Accessible:**
1. Check if the service is running on the correct port
2. Verify firewall settings allow access to port 5001
3. Check if another service is using port 5001

**Service Fails to Start:**
1. Check Python dependencies are installed
2. Verify the virtual environment is set up correctly
3. Check file permissions and ownership


-----------------------------------------------------------------------------------


## Information

<details>

<summary>Display Settings from RGBLEDMatrix Library</summary>


## Display Settings
If you are copying my setup, you can likely leave this alone. 
- hardware: Configures how the matrix is driven.
  - rows, cols, chain_length: Physical panel configuration.
  - brightness: Display brightness (0–100).
  - hardware_mapping: Use "adafruit-hat-pwm" for Adafruit bonnet WITH the jumper mod. Remove -pwm if you did not solder the jumper.
  - pwm_bits, pwm_dither_bits, pwm_lsb_nanoseconds: Affect color fidelity.
  - limit_refresh_rate_hz: Cap refresh rate for better stability.
- runtime:
  - gpio_slowdown: Tweak this depending on your Pi model. Match it to the generation (e.g., Pi 3 → 3, Pi 4 -> 4).
- display_durations:
  - Control how long each display module stays visible in seconds. For example, if you want more focus on stocks, increase that value.
### Modules
- Each module (weather, stocks, crypto, calendar, etc.) has enabled, update_interval, and often display_format settings.
- Sports modules also support test_mode, live_update_interval, and favorite_teams.
- Logos are loaded from the logo_dir path under assets/sports/...

</details>


<details>

<summary>Cache Information</summary>


### Persistent Caching Setup

The LEDMatrix system uses persistent caching to improve performance and reduce API calls. When running with `sudo`, the system needs a persistent cache directory that survives restarts.

**First-Time Setup:**
Run the setup script to create a persistent cache directory:
```bash
chmod +x setup_cache.sh
./setup_cache.sh
```

This will:
- Create `/var/cache/ledmatrix/` directory
- Set proper ownership to your user account
- Set permissions to allow the daemon user (which the system runs as) to write
- Test writability for both your user and the daemon user

**If You Still See Cache Warnings:**
If you see warnings about using temporary cache directory, run the permissions fix:
```bash
chmod +x scripts/fix_perms/fix_cache_permissions.sh
./scripts/fix_perms/fix_cache_permissions.sh
```

**Manual Setup:**
If you prefer to set up manually:
```bash
sudo mkdir -p /var/cache/ledmatrix
sudo chown $USER:$USER /var/cache/ledmatrix
sudo chmod 777 /var/cache/ledmatrix
```

**Cache Locations (in order of preference):**
1. `~/.ledmatrix_cache/` (user's home directory) - **Most persistent**
2. `/var/cache/ledmatrix/` (system cache directory) - **Persistent across restarts**
3. `/opt/ledmatrix/cache/` (alternative persistent location)
4. `/tmp/ledmatrix_cache/` (temporary directory) - **NOT persistent**

**Note:** If the system falls back to `/tmp/ledmatrix_cache/`, you'll see a warning message and the cache will not persist across restarts.


## Caching System

The LEDMatrix system includes a robust caching mechanism to optimize API calls and reduce network traffic:

### Cache Location
- Default cache directory: `/tmp/ledmatrix_cache`
- Cache files are stored with proper permissions (755 for directories, 644 for files)
- When running as root/sudo, cache ownership is automatically adjusted to the real user

### Cached Data Types
- Weather data (current conditions and forecasts)
- Stock prices and market data
- Stock news headlines
- ESPN game information

### Cache Behavior
- Data is cached based on update intervals defined in `config.json`
- Cache is automatically invalidated when:
  - Update interval has elapsed
  - Market is closed (for stock data)
  - Data has changed significantly
- Failed API calls fall back to cached data when available
- Cache files use atomic operations to prevent corruption

### Cache Management
- Cache files are automatically created and managed
- No manual intervention required
- Cache directory is created with proper permissions on first run
- Temporary files are used for safe updates
- JSON serialization handles all data types including timestamps

</details>



<details>

<summary>Date Format Configuration </summary>

## Date Format Configuration

You can customize the date format for upcoming games across all sports displays. The `use_short_date_format` setting in `config/config.json` under the `display` section controls this behavior.

- **`"use_short_date_format": true`**: Displays dates in a short, numerical format (e.g., "8/30").
- **`"use_short_date_format": false`** (Default): Displays dates in a more descriptive format with an ordinal suffix (e.g., "Aug 30th").

### Example `config.json`

```json
"display": {
    "hardware": {
        ...
    },
    "runtime": {
        ...
    },
    "display_durations": {
        ...
    },
    "use_short_date_format": false // Set to true for "8/30" format
},
```

</details>


-----------------------------------------------------------------------------------

<details>

<summary>Passwordless Sudo for Web Interface Actions</summary>

## Granting Passwordless Sudo Access for Web Interface Actions

The web interface needs to run certain commands with `sudo` (e.g., `reboot`, `systemctl start/stop/enable/disable ledmatrix.service`, `python display_controller.py`). To avoid needing to enter a password for these actions through the web UI, you can configure the `sudoers` file to allow the user running the Flask application to execute these specific commands without a password.

1. Shortcut to automate the below steps:
```chmod +x configure_web_sudo.sh```
then
```./configure_web_sudo.sh```


Manual Method:

**WARNING: Be very careful when editing the `sudoers` file. Incorrect syntax can lock you out of `sudo` access.**

1.  **Identify the user:** Determine which user is running the `web_interface.py` script. Often, this might be the default user like `pi` on a Raspberry Pi, or a dedicated user you've set up.

2.  **Open the sudoers file for editing:**
    Use the `visudo` command, which locks the sudoers file and checks for syntax errors before saving.
    ```bash
    sudo visudo
    ```

3.  **Add the permission lines:**
    Scroll to the bottom of the file and add lines similar to the following. Replace `your_flask_user` with the actual username running the Flask application.
    You'll need to specify the full paths to the commands. You can find these using the `which` command (e.g., `which python`, `which systemctl`, `which reboot`).

    ```sudoers
    # Allow your_flask_user to run specific commands without a password for the LED Matrix web interface
    your_flask_user ALL=(ALL) NOPASSWD: /sbin/reboot
    your_flask_user ALL=(ALL) NOPASSWD: /bin/systemctl start ledmatrix.service
    your_flask_user ALL=(ALL) NOPASSWD: /bin/systemctl stop ledmatrix.service
    your_flask_user ALL=(ALL) NOPASSWD: /bin/systemctl enable ledmatrix.service
    your_flask_user ALL=(ALL) NOPASSWD: /bin/systemctl disable ledmatrix.service
    your_flask_user ALL=(ALL) NOPASSWD: /usr/bin/python /path/to/your/display_controller.py 
    your_flask_user ALL=(ALL) NOPASSWD: /bin/bash /path/to/your/stop_display.sh
    ```
    *   **Important:**
        *   Replace `your_flask_user` with the correct username.
        *   Replace `/path/to/your/display_controller.py` with the absolute path to your `display_controller.py` script.
        *   Replace `/path/to/your/stop_display.sh` with the absolute path to your `stop_display.sh` script.
        *   The paths to `python`, `systemctl`, `reboot`, and `bash` might vary slightly depending on your system. Use `which <command>` to find the correct paths if you are unsure. For example, `which python` might output `/usr/bin/python3` - use that full path.

4.  **Save and Exit:**
    *   If you're in `nano` (common default for `visudo`): `Ctrl+X`, then `Y` to confirm, then `Enter`.
    *   If you're in `vim`: `Esc`, then `:wq`, then `Enter`.

    `visudo` will check the syntax. If there's an error, it will prompt you to re-edit or quit. **Do not quit without fixing errors if possible.**

5.  **Test:**
    After saving, try running one of the specified commands as `your_flask_user` using `sudo` from a regular terminal session to ensure it doesn't ask for a password.
    For example:
    ```bash
    sudo -u your_flask_user sudo /sbin/reboot
    ```
    (Don't actually reboot if you're not ready, but it should proceed without a password prompt if configured correctly. You can test with a less disruptive command like `sudo -u your_flask_user sudo systemctl status ledmatrix.service`).

**Security Considerations:**
Granting passwordless `sudo` access, even for specific commands, has security implications. Ensure that the scripts and commands allowed are secure and cannot be easily exploited. The web interface itself should also be secured if it's exposed to untrusted networks.
For `display_controller.py` and `stop_display.sh`, ensure their file permissions restrict write access to only trusted users, preventing unauthorized modification of these scripts which run with elevated privileges.

</details>


## Web Interface V2 (simplified quick start)

### 1) Run the helper (does the above and starts the server):
```
python3 start_web_v2.py
```

### 2) Start the web UI v2
```
python web_interface_v2.py
```

### 3) Autostart (recommended)
Set `"web_display_autostart": true` in `config/config.json`.
Ensure your systemd service calls `start_web_conditionally.py` (installed by `install_service.sh`).

### 4) Permissions (optional but recommended)
- Add the service user to `systemd-journal` for viewing logs without sudo.
- Configure passwordless sudo for actions (start/stop service, reboot, shutdown) if desired.
    - Required for web Ui actions, look in the section above for the commands to run (chmod +x configure_web_sudo.sh & ./configure_web_sudo.sh)




## Final Notes
- Most configuration is done via config/config.json
- Refresh intervals for sports/weather/stocks are customizable
- A caching system reduces API strain and helps ensure the display doesn't hammer external services (and ruin it for everyone)
- Font files should be placed in assets/fonts/
- You can test each module individually for debugging


##What's Next?
- Adding MQTT/HomeAssistant integration



### If you've read this far — thanks!  
