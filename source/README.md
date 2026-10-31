# A-leggo Research source

This folder contains the source for the A-leggo Research application: a Python backend, Next.js web interface, and Tauri desktop shell.

## Development setup

Recommended tools on Windows are Python 3.12, Node.js 24, and Git. Python 3.11 through 3.14 are declared in `pyproject.toml`. Rust and the Visual Studio C++ build tools are needed only to rebuild the Tauri desktop installer.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Push-Location web
npm ci
Pop-Location
```

Run the local application with `aleggo start`. For web-only development, run `npm run dev` from `web`; start the backend separately with `aleggo serve --port 8001` if needed.

## Project layout

- `aleggo/`: Python backend, research workflow, APIs, and mathematics capabilities
- `web/`: Next.js frontend and lockfile
- `desktop/`: Tauri desktop shell
- `tests/`: automated checks
- `AGENTS.md`: architecture and contributor notes

Runtime data belongs in the local `data/` directory and is excluded from version control. Configure provider credentials in the application; never commit API keys or local settings.

## License

Apache License 2.0. Third-party attributions are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
