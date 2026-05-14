# Pull Request

## Summary

<!-- 1-3 sentences describing what this PR does and why. -->

## Type of change

<!-- Check all that apply. -->

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor (no functional change)
- [ ] Build / CI
- [ ] Plugin work (link to the plugin)

## Related issues

<!-- "Fixes #123" or "Refs #123". Use "Fixes" for bug PRs so the issue
auto-closes when this merges. -->

## Test plan

<!-- How did you test this? Check all that apply. Add details for any
checked box. -->

- [ ] Ran on a real Raspberry Pi with hardware
- [ ] Ran in emulator mode (`EMULATOR=true python3 run.py`)
- [ ] Ran the dev preview server (`scripts/dev_server.py`)
- [ ] Ran the test suite (`pytest`)
- [ ] Manually verified the affected code path in the web UI
- [ ] N/A — documentation-only change

## Documentation

- [ ] I updated `README.md` if user-facing behavior changed
- [ ] I updated the relevant doc in `docs/` if developer behavior changed
- [ ] I added/updated docstrings on new public functions
- [ ] N/A — no docs needed

## Plugin compatibility

<!-- For changes to BasePlugin, the plugin loader, the web UI, or the
config schema. -->

- [ ] No plugin breakage expected
- [ ] Some plugins will need updates — listed below
- [ ] N/A — change doesn't touch the plugin system

## Checklist

- [ ] My commits follow the message convention in `CONTRIBUTING.md`
- [ ] I read `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`
- [ ] I've not committed any secrets or hardcoded API keys
- [ ] If this adds a new config key, the form in the web UI was
      verified (the form is generated from `config_schema.json`)

## Notes for reviewer

<!-- Anything reviewers should know — gotchas, things you weren't
sure about, decisions you'd like a second opinion on. -->
