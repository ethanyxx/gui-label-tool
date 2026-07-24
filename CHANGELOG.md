# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog], and this project adheres to [Semantic Versioning] and
[PEP 440].

## [Unreleased]

### Added
- React + Vite + Tailwind web console served by the frontend service
  (FastAPI), replacing the Gradio UI: live event stream with screenshot
  thumbnails, per-event exclude/restore, session restore after page reload.
- `LICENSE` (MIT).

### Changed
- The frontend service is now a FastAPI app that serves the prebuilt SPA from
  `gui_label_tool/frontend/static/` and exposes a small `/api` for the browser;
  screenshots are served as static files instead of inlined base64.
- `gradio` removed from the runtime dependencies.

### Fixed
- Annotation service could get stuck "active with no session" when `finalize`
  was called without a prior `freeze` (`is_active` is now always reset when the
  session state is cleared).
- `scripts/manage_services.sh` shipped without the executable bit.

## [0.1.0] - 2025-12-04

### Added
- Initial project.

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/
[PEP 440]: https://peps.python.org/pep-0440/
