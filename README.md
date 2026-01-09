# LEDMatrix


### Setup video and feature walkthrough on Youtube (Outdated but still useful) : 
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
- Hzeller @ [GitHub](https://github.com/hzeller/rpi-rgb-led-matrix) for his groundwork on controlling an LED Matrix from the Raspberry Pi
- Basmilius @ [GitHub](https://github.com/basmilius/weather-icons/) for his free and extensive weather icons
- nvstly @ [GitHub](https://github.com/nvstly/icons) for their Stock and Crypto Icons
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

## Plugins

LEDMatrix uses a plugin-based architecture where all display functionality (except the core calendar) is implemented as plugins. All managers that were previously built into the core system are now available as plugins through the Plugin Store.

### Plugin Store

The easiest way to discover and install plugins is through the **Plugin Store** in the LEDMatrix web interface:

1. Open the web interface (`http://your-pi-ip:5000`)
2. Navigate to the **Plugin Manager** tab
3. Browse available plugins in the Plugin Store
4. Click **Install** on any plugin you want
5. Configure and enable plugins through the web UI

### Installing 3rd-Party Plugins

You can also install plugins directly from GitHub repositories:

- **Single Plugin**: Install from any GitHub repository URL
- **Registry/Monorepo**: Install multiple plugins from a single repository

See the [Plugin Store documentation](https://github.com/ChuckBuilds/ledmatrix-plugins) for detailed installation instructions.

For plugin development, check out the [Hello World Plugin](https://github.com/ChuckBuilds/ledmatrix-hello-world) repository as a starter template.

## ⚠️ Breaking Changes

**Important for users upgrading from older versions:**

1. **Script Path Reorganization**: Installation scripts have been moved to `scripts/install/`:
   - `./install_service.sh` → `./scripts/install/install_service.sh`
   - `./install_web_service.sh` → `./scripts/install/install_web_service.sh`
   - `./configure_web_sudo.sh` → `./scripts/install/configure_web_sudo.sh`
   
   If you have automation, cron jobs, or custom tooling that references these scripts, you **must** update them to use the new paths. See the [Migration Guide](MIGRATION_GUIDE.md) for complete details.

2. **Built-in Managers Deprecated**: The built-in managers (hockey, football, stocks, etc.) are now deprecated and have been moved to the plugin system. **You must install replacement plugins from the Plugin Store** in the web interface instead. The plugin system provides the same functionality with better maintainability and extensibility.

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
I 3D printed stands to keep the panels upright and snug. STL Files are included in the Repo but are also available at https://www.thingiverse.com/thing:5169867 Thanks to "Randomwire" for making these for the 4mm Pixel Pitch LED Matrix.

Special Thanks for Rmatze for making:
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
3. For Operating System (OS), choose "Other", then choose Raspbian OS (64-bit) Lite 
![image](https://github.com/user-attachments/assets/e8e2e806-18a8-4175-9c25-0cefaae438ea)
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

## Quick Install (Recommended)

SSH into your Raspberry Pi and paste this single command:

```bash
curl -fsSL https://raw.githubusercontent.com/ChuckBuilds/LEDMatrix/main/scripts/install/one-shot-install.sh | bash
```

This one-shot installer will automatically:
- Check system prerequisites (network, disk space, sudo access)
- Install required system packages (git, python3, build tools, etc.)
- Clone or update the LEDMatrix repository
- Run the complete first-time installation script

The installation process typically takes 10-30 minutes depending on your internet connection and Pi model. All errors are reported explicitly with actionable fixes.

**Note:** The script is safe to run multiple times and will handle existing installations gracefully.

<details>

<summary>Manual Installation (Alternative)</summary>

If you prefer to install manually or the one-shot installer doesn't work for your setup:

1. SSH into your Raspberry Pi:
```bash
ssh ledpi@ledpi
```

2. Update repositories, upgrade Raspberry Pi OS, and install prerequisites:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip cython3 build-essential python3-dev python3-pillow scons
```

3. Clone this repository:
```bash
git clone https://github.com/ChuckBuilds/LEDMatrix.git
cd LEDMatrix
```

4. Run the first-time installation script:
```bash
chmod +x first_time_install.sh
sudo bash ./first_time_install.sh
```

This single script installs services, dependencies, configures permissions and sudoers, and validates the setup.

</details>

</details>


## Configuration

<details>

<summary>Configuration</summary>

## Configuration

### Initial Setup

Edit the project via the web interface at http://ledpi:5000  or for manual control:

1. **First-time setup**: The previous "First_time_install.sh" script should've already copied the template to create your config.json:

2. **Edit your configuration**: 
   ```bash
   sudo nano config/config.json
   ```
or 

3. **Having Issues?**: Run the First Time Script again:
  ```bash
  sudo ./first_time_install.sh
  ```


### Automatic Configuration Migration

The system automatically handles configuration updates:
- **New installations**: Creates `config.json` from the template automatically
- **Existing installations**: Automatically adds new configuration options with default values when the system starts
- **Backup protection**: Creates a backup of your current config before applying updates
- **No conflicts**: Your custom settings are preserved while new options are added

Everything is configured via `config/config.json` and `config/config_secrets.json`. The `config.json` file is not tracked by Git to prevent conflicts during updates.

</details>


------------------------------------------------------------------------------------

## Running the Display

I recommend using the web-ui to control the Display but you can also run the following commands via ssh:

From the project root directory:
```bash
sudo python3 display_controller.py
```
This will start the display cycle but only stays active as long as your ssh session is active.


-----------------------------------------------------------------------------------

<details>

<summary>Run on Startup Automatically with Systemd Service Installation</summary>


## Run on Startup Automatically with Systemd Service Installation
The first time install will handle this:
The LEDMatrix can be installed as a systemd service to run automatically at boot and be managed easily. The service runs as root to ensure proper hardware timing access for the LED matrix.

### Installing the Service (this is included in the first_time_install.sh)

1. Make the install script executable:
```bash
chmod +x scripts/install/install_service.sh
```

2. Run the install script with sudo:
```bash
sudo ./scripts/install/install_service.sh
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

## Web Interface Installation
The first time install will handle this:
The LEDMatrix system includes Web Interface that runs on port 5000 and provides real-time display preview, configuration management, and on-demand display controls.

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
http://your-pi-ip:5000
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

**Port 5000 Not Accessible:**
1. Check if the service is running on the correct port
2. Verify firewall settings allow access to port 5000
3. Check if another service is using port 5000

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

-----------------------------------------------------------------------------------




### If you've read this far — thanks!  
