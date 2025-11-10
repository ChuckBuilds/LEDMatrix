# Plugin Web UI Actions

## Overview

Plugins can define custom action buttons in their configuration page through the `web_ui_actions` field in their `manifest.json`. This allows plugins to provide authentication flows, setup wizards, or other interactive actions without requiring changes to the main web UI.

## Manifest Schema

Add a `web_ui_actions` array to your plugin's `manifest.json`:

```json
{
  "id": "your-plugin",
  "name": "Your Plugin",
  ...
  "web_ui_actions": [
    {
      "id": "action-id",
      "type": "script",
      "title": "Action Title",
      "description": "Brief description of what this action does",
      "button_text": "Execute Action",
      "icon": "fas fa-icon-name",
      "color": "blue",
      "script": "path/to/script.py",
      "oauth_flow": false,
      "section_description": "Optional section description",
      "success_message": "Action completed successfully",
      "error_message": "Action failed",
      "step1_message": "Authorization URL generated",
      "step2_prompt": "Please paste the full redirect URL:",
      "step2_button_text": "Complete Authentication"
    }
  ]
}
```

## Action Properties

### Required Fields

- **`id`**: Unique identifier for this action (used in API calls)
- **`type`**: Action type - currently only `"script"` is supported
- **`script`**: Path to the Python script relative to plugin directory

### Optional Fields

- **`title`**: Display title for the action (defaults to `id`)
- **`description`**: Description shown below the title
- **`button_text`**: Text for the action button (defaults to `title`)
- **`icon`**: FontAwesome icon class (e.g., `"fab fa-spotify"`)
- **`color`**: Color theme - `"blue"`, `"green"`, `"red"`, `"yellow"`, `"purple"`, etc. (defaults to `"blue"`)
- **`oauth_flow`**: Set to `true` for OAuth-style two-step authentication flows
- **`section_description`**: Description shown at the top of the actions section
- **`success_message`**: Message shown on successful completion
- **`error_message`**: Message shown on failure
- **`step1_message`**: Message shown after step 1 (for OAuth flows)
- **`step2_prompt`**: Prompt text for step 2 redirect URL input
- **`step2_button_text`**: Button text for step 2 (defaults to "Complete Authentication")

## Action Types

### Script Actions (`type: "script"`)

Executes a Python script from the plugin directory. The script receives:
- `LEDMATRIX_ROOT` environment variable set to the project root
- Access to plugin directory and config files

#### Simple Script Action

For single-step actions (e.g., YouTube Music authentication):

```json
{
  "id": "authenticate-ytm",
  "type": "script",
  "title": "YouTube Music Authentication",
  "description": "Authenticate with YouTube Music",
  "button_text": "Authenticate YTM",
  "icon": "fab fa-youtube",
  "color": "red",
  "script": "authenticate_ytm.py"
}
```

#### OAuth Flow Script Action

For two-step OAuth flows (e.g., Spotify):

```json
{
  "id": "authenticate-spotify",
  "type": "script",
  "title": "Spotify Authentication",
  "description": "Authenticate with Spotify",
  "button_text": "Authenticate Spotify",
  "icon": "fab fa-spotify",
  "color": "green",
  "script": "authenticate_spotify.py",
  "oauth_flow": true,
  "step1_message": "Authorization URL generated",
  "step2_prompt": "Please paste the full redirect URL from Spotify after authorization:",
  "step2_button_text": "Complete Authentication"
}
```

For OAuth flows, the script should:
1. **Step 1**: Export a function or pattern that can generate an auth URL
   - Option 1: Define `get_auth_url()` function
   - Option 2: Define `load_spotify_credentials()` function (Spotify-specific pattern)
2. **Step 2**: Accept redirect URL via stdin and complete authentication

## Example: Music Plugin

Here's a complete example for the `ledmatrix-music` plugin:

```json
{
  "id": "ledmatrix-music",
  "name": "Music Player - Now Playing",
  ...
  "web_ui_actions": [
    {
      "id": "authenticate-spotify",
      "type": "script",
      "title": "Spotify Authentication",
      "description": "Click to authenticate with Spotify",
      "button_text": "Authenticate Spotify",
      "icon": "fab fa-spotify",
      "color": "green",
      "script": "authenticate_spotify.py",
      "oauth_flow": true,
      "section_description": "Authenticate with Spotify or YouTube Music to enable music playback display.",
      "success_message": "Spotify authentication completed successfully",
      "error_message": "Spotify authentication failed",
      "step1_message": "Authorization URL generated",
      "step2_prompt": "Please paste the full redirect URL from Spotify after authorization:",
      "step2_button_text": "Complete Authentication"
    },
    {
      "id": "authenticate-ytm",
      "type": "script",
      "title": "YouTube Music Authentication",
      "description": "Click to authenticate with YouTube Music",
      "button_text": "Authenticate YTM",
      "icon": "fab fa-youtube",
      "color": "red",
      "script": "authenticate_ytm.py",
      "success_message": "YouTube Music authentication completed successfully",
      "error_message": "YouTube Music authentication failed"
    }
  ]
}
```

## How It Works

1. **Plugin defines actions** in `manifest.json` → `web_ui_actions`
2. **API loads actions** → `/api/v3/plugins/installed` includes `web_ui_actions` in response
3. **Frontend renders buttons** → Configuration form dynamically generates action buttons
4. **User clicks button** → Calls `/api/v3/plugins/action` with `plugin_id` and `action_id`
5. **Backend executes** → Runs script or performs action based on type
6. **Result displayed** → Success/error message shown to user

## Benefits

- ✅ **No web UI changes needed** - Plugins define their own actions
- ✅ **Extensible** - Easy to add new action types in the future
- ✅ **Consistent UX** - All actions follow the same UI patterns
- ✅ **Plugin-specific** - Each plugin controls its own authentication/setup flows
- ✅ **Future-proof** - Can add endpoint-based actions, webhooks, etc.

## Future Enhancements

- **Endpoint actions**: Call plugin-defined HTTP endpoints
- **Webhook actions**: Trigger webhook URLs
- **Form actions**: Collect additional input before execution
- **Batch actions**: Execute multiple actions in sequence

