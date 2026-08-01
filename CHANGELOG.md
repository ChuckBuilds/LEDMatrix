# Changelog

Notable changes to the LEDMatrix core. The version below is the value of
`src.__version__`, which the plugin loader reports to compatibility checks and
which plugin manifests reference via `ledmatrix_min_version`.

**Why this file exists:** the plugin monorepo bundles fallback copies of several
core modules (see `docs/plugin-development/08-shared-sports-code.md` in
[ledmatrix-plugins](https://github.com/ChuckBuilds/ledmatrix-plugins)). A plugin
may delete its bundled copy only when its manifest floors on the first core
release that ships the module — which requires module additions to be recorded
here, against a version number. When you add a module plugins will import via
`src.*`, note it in the Unreleased section and bump `src/__init__.py` in the
release that ships it.

## Unreleased

### Added
- `src/element_style.py` — per-element style resolver backing the
  `x-style-elements` config-schema extension. Already consumed (behind guarded
  imports with classic fallbacks) by the `of-the-day`, `ledmatrix-music`, and
  `football-scoreboard` plugins.
- Core unit-test CI job enrolling the previously unenrolled suites (skin
  system, data sources, API extractors, scroll helper, adaptive layout, loader
  compatibility warning) plus new characterization tests for
  `src/base_classes/sports.py` ahead of the shared sports-code unification.

### Fixed
- `FontManager` resolves `assets/fonts` against the core install root instead
  of the process working directory, so font loading works when the process
  starts elsewhere (e.g. the plugin safety harness on CI).

## 3.1.0

Baseline for this changelog. Highlights already shipped at this version:
skin system for sports scoreboards (#419), Vegas continuous-scroll overhaul
(#423), plugin update surfacing (#421).
