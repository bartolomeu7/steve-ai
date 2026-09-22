# SteveLauncher - Minimal Launcher for Steve Desktop Assistant

## Overview

SteveLauncher.exe is a small Windows executable that launches the existing Steve Desktop Assistant installation. It does NOT bundle any Steve code, dependencies, or resources - it only finds and executes the real installation.

## Location

```
STEVE AI/
├── launcher/
│   ├── SteveLauncher.py      # Source code
│   ├── dist/
│   │   └── SteveLauncher.exe # Compiled executable (Windows)
│   └── README.md             # This file
├── main.py                   # Steve's entrypoint (NOT bundled)
├── .venv/                    # Python virtual environment (NOT bundled)
└── ... (rest of Steve project)
```

## How It Works

1. **Locates the project root**: The launcher detects its location (compiled .exe or .py script) and navigates up to find the project root.

2. **Finds Python**: Looks for `.venv/Scripts/python.exe` in the project root.

3. **Finds main.py**: Looks for `main.py` in the project root.

4. **Launches Steve**: Runs `python.exe main.py` with the correct working directory using `CREATE_NO_WINDOW` flag.

## Why Not Bundle the Entire Project?

- **Updates**: When you modify Steve's code, the launcher automatically uses the new version without recompilation.
- **Size**: The launcher is ~7 MB; bundling Steve would be 100+ MB.
- **Flexibility**: You can still modify Steve's code directly.
- **Performance**: No need to extract bundled files on each launch.

## Usage

### Double-click SteveLauncher.exe

Just double-click the executable. Steve will start normally with the GUI.

### Command-line arguments

```bash
# Start in GUI mode (default)
SteveLauncher.exe

# Start in CLI mode
SteveLauncher.exe --cli
```

## Requirements

- Steve Desktop Assistant installed in the parent directory
- Python 3.11+ with `.venv` configured
- All Steve dependencies installed in `.venv`

## Rebuilding the Launcher

If you need to rebuild the launcher (e.g., after modifying `SteveLauncher.py`):

```bash
cd "C:\Users\HAKARI\Downloads\STEVE AI\launcher"
..\venv\Scripts\pip.exe install pyinstaller  # If not already installed
..\venv\Scripts\pyinstaller.exe --onefile --name SteveLauncher --clean SteveLauncher.py
```

The new executable will be in `dist/SteveLauncher.exe`.

## Running Manually (Without Compilation)

You can run the launcher directly with Python:

```bash
cd "C:\Users\HAKARI\Downloads\STEVE AI\launcher"
..\venv\Scripts\python.exe SteveLauncher.py
```

## Error Handling

The launcher shows Windows message box errors if:

- The Steve project directory is not found
- `main.py` is not found
- `.venv` is not found
- Python executable is not found
- An error occurs while starting Steve

## Technical Details

- **Language**: Python 3.11
- **Compilation**: PyInstaller (one-file mode)
- **Dependencies**: ctypes (Python standard library, for Windows message boxes)
- **Size**: ~7 MB (includes Python runtime)
- **No Steve code bundled**: Only the launcher logic is compiled

## Troubleshooting

### "Python nao encontrado"

Ensure `.venv` exists and is properly configured. Run:

```bash
cd "C:\Users\HAKARI\Downloads\STEVE AI"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### "main.py nao encontrado"

Ensure `main.py` exists in the project root directory.

### Steve doesn't start

1. Check if Ollama is running
2. Check the logs in `logs/steve.log`
3. Try running manually: `.venv\Scripts\python.exe main.py`
