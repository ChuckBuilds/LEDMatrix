# Contributing to LEDMatrix

Thanks for considering a contribution! LEDMatrix is built with help from
the community and we welcome bug reports, plugins, documentation
improvements, and code changes.

## Quick links

- **Bugs / feature requests**: open an issue using one of the templates
  in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).
- **Real-time discussion**: the
  [LEDMatrix Discord](https://discord.gg/uW36dVAtcT).
- **Plugin development**:
  [`docs/PLUGIN_DEVELOPMENT_GUIDE.md`](docs/PLUGIN_DEVELOPMENT_GUIDE.md)
  and the [`ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins)
  repository.
- **Security issues**: see [`SECURITY.md`](SECURITY.md). Please don't
  open public issues for vulnerabilities.

## Setting up a development environment

1. Clone with submodules:
   ```bash
   git clone --recurse-submodules https://github.com/ChuckBuilds/LEDMatrix.git
   cd LEDMatrix
   ```
2. For development without hardware, run the dev preview server:
   ```bash
   python3 scripts/dev_server.py
   # then open http://localhost:5001
   ```
   See [`docs/DEV_PREVIEW.md`](docs/DEV_PREVIEW.md) for details.
3. To run the full display in emulator mode:
   ```bash
   EMULATOR=true python3 run.py
   ```
4. To target real hardware on a Raspberry Pi, follow the install
   instructions in the root [`README.md`](README.md).

## Running the tests

```bash
pip install -r requirements.txt
pytest
```

See [`docs/HOW_TO_RUN_TESTS.md`](docs/HOW_TO_RUN_TESTS.md) for details
on test markers, the per-plugin tests, and the web-interface
integration tests.

## Submitting changes

1. **Open an issue first** for non-trivial changes. This avoids
   wasted work on PRs that don't fit the project direction.
2. **Create a topic branch** off `main`:
   `feat/<short-description>`, `fix/<short-description>`,
   `docs/<short-description>`.
3. **Keep PRs focused.** One conceptual change per PR. If you find
   adjacent bugs while working, fix them in a separate PR.
4. **Follow the existing code style.** Python code uses standard
   `black`/`ruff` conventions; HTML/JS in `web_interface/` follows the
   patterns already in `templates/v3/` and `static/v3/`.
5. **Update documentation** alongside code changes. If you add a
   config key, document it in the relevant `*.md` file (or, for
   plugins, in `config_schema.json` so the form is auto-generated).
6. **Run the tests** locally before opening the PR.
7. **Use the PR template** — `.github/PULL_REQUEST_TEMPLATE.md` will
   prompt you for what we need.

## Commit message convention

Conventional Commits is encouraged but not strictly enforced:

- `feat: add NHL playoff bracket display`
- `fix(plugin-loader): handle missing class_name in manifest`
- `docs: correct web UI port in TROUBLESHOOTING.md`
- `refactor(cache): consolidate strategy lookup`

Keep the subject under 72 characters; put the why in the body.

## Contributing a plugin

LEDMatrix plugins live in their own repository:
[`ledmatrix-plugins`](https://github.com/ChuckBuilds/ledmatrix-plugins).
Plugin contributions go through that repo's
[`SUBMISSION.md`](https://github.com/ChuckBuilds/ledmatrix-plugins/blob/main/SUBMISSION.md)
process. The
[`hello-world` plugin](https://github.com/ChuckBuilds/ledmatrix-plugins/tree/main/plugins/hello-world)
is the canonical starter template.

## Reviewing pull requests

Maintainer review is by [@ChuckBuilds](https://github.com/ChuckBuilds).
Community review is welcome on any open PR — leave constructive
comments, test on your hardware if applicable, and call out anything
unclear.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to abide by its terms.

## License

LEDMatrix is licensed under the [GNU General Public License v3.0 or
later](LICENSE). By submitting a contribution you agree to license it
under the same terms (the standard "inbound = outbound" rule that
GitHub applies by default).

LEDMatrix builds on
[`rpi-rgb-led-matrix`](https://github.com/hzeller/rpi-rgb-led-matrix),
which is GPL-2.0-or-later. The "or later" clause makes it compatible
with GPL-3.0 distribution.
