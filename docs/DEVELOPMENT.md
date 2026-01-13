# Development Guide

This guide provides information for developers and contributors working on the LEDMatrix project.

## Git Submodules

### rpi-rgb-led-matrix-master Submodule

The `rpi-rgb-led-matrix-master` submodule is a foundational dependency located at the repository root (not in `plugins/`). This submodule provides the core hardware abstraction layer for controlling RGB LED matrices via the Raspberry Pi GPIO pins.

#### Architectural Rationale

**Why at the root?**
- **Core Dependency**: Unlike plugins in the `plugins/` directory, `rpi-rgb-led-matrix-master` is a foundational library required by the core LEDMatrix system, not an optional plugin
- **System-Level Integration**: The `rgbmatrix` Python module (built from this submodule) is imported by `src/display_manager.py`, which is part of the core display system
- **Build Requirements**: The submodule must be compiled to create the `rgbmatrix` Python bindings before the system can run
- **Separation of Concerns**: Keeping core dependencies at the root level separates them from user-installable plugins, maintaining a clear architectural distinction

**Why not in `plugins/`?**
- Plugins are optional, user-installable modules that depend on the core system
- `rpi-rgb-led-matrix-master` is a required build dependency, not an optional plugin
- The core system cannot function without this dependency

#### Initializing the Submodule

When cloning the repository, you must initialize the submodule:

**First-time clone (recommended):**
```bash
git clone --recurse-submodules https://github.com/ChuckBuilds/LEDMatrix.git
cd LEDMatrix
```

**If you already cloned without submodules:**
```bash
git submodule update --init --recursive
```

**To initialize only the rpi-rgb-led-matrix-master submodule:**
```bash
git submodule update --init --recursive rpi-rgb-led-matrix-master
```

#### Building the Submodule

After initializing the submodule, you need to build the Python bindings:

```bash
cd rpi-rgb-led-matrix-master
make build-python
cd bindings/python
python3 -m pip install --break-system-packages .
```

**Note:** The `first_time_install.sh` script automates this process during installation.

#### Troubleshooting

**Submodule appears empty:**
If the `rpi-rgb-led-matrix-master` directory exists but is empty or lacks a `Makefile`:
```bash
# Remove the empty directory
rm -rf rpi-rgb-led-matrix-master

# Re-initialize the submodule
git submodule update --init --recursive rpi-rgb-led-matrix-master
```

**Build fails:**
Ensure you have the required build dependencies installed:
```bash
sudo apt install -y build-essential python3-dev cython3 scons
```

**Import error for `rgbmatrix` module:**
- Verify the submodule is initialized: `ls rpi-rgb-led-matrix-master/Makefile`
- Ensure the Python bindings are built and installed (see "Building the Submodule" above)
- Check that the module is installed: `python3 -c "from rgbmatrix import RGBMatrix; print('OK')"`

**Submodule out of sync:**
If the submodule commit doesn't match what the main repository expects:
```bash
git submodule update --remote rpi-rgb-led-matrix-master
```

#### CI/CD Configuration

When setting up CI/CD pipelines, ensure submodules are initialized before building:

**GitHub Actions Example:**
```yaml
- name: Checkout repository
  uses: actions/checkout@v3
  with:
    submodules: recursive

- name: Build rpi-rgb-led-matrix
  run: |
    cd rpi-rgb-led-matrix-master
    make build-python
    cd bindings/python
    pip install .
```

**GitLab CI Example:**
```yaml
variables:
  GIT_SUBMODULE_STRATEGY: recursive

build:
  script:
    - cd rpi-rgb-led-matrix-master
    - make build-python
    - cd bindings/python
    - pip install .
```

**Jenkins Example:**
```groovy
stage('Checkout') {
    checkout([
        $class: 'GitSCM',
        branches: [[name: '*/main']],
        doGenerateSubmoduleConfigurations: false,
        extensions: [[$class: 'SubmoduleOption',
                      disableSubmodules: false,
                      parentCredentials: true,
                      recursiveSubmodules: true,
                      reference: '',
                      trackingSubmodules: false]],
        userRemoteConfigs: [[url: 'https://github.com/ChuckBuilds/LEDMatrix.git']]
    ])
}
```

**General CI/CD Checklist:**
- ✓ Use `--recurse-submodules` flag when cloning (or equivalent in your CI system)
- ✓ Initialize submodules before any build steps
- ✓ Build the Python bindings if your tests require the `rgbmatrix` module
- ✓ Note: Emulator mode (using `RGBMatrixEmulator`) doesn't require the submodule to be built

---

## Plugin Submodules

Plugin submodules are located in the `plugins/` directory and are managed similarly:

**Initialize all plugin submodules:**
```bash
git submodule update --init --recursive plugins/
```

**Initialize a specific plugin:**
```bash
git submodule update --init --recursive plugins/hockey-scoreboard
```

For more information about plugins, see the [Plugin Development Guide](.cursor/plugins_guide.md) and [Plugin Architecture Specification](docs/PLUGIN_ARCHITECTURE_SPEC.md).

