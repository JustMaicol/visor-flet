# Protocol Finder

Search and compile deed pages in PDF documents. Desktop app built with [Flet](https://flet.dev) (Python) and PyMuPDF.

## Requirements

- Windows 10/11
- Python 3.12 or newer (developed with 3.14)
- Git

## Installation

```powershell
git clone <REPOSITORY-URL>
cd visor-flet

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Running

```powershell
python main.py
```

## Building a Windows executable

Product settings live in `pyproject.toml` (`[tool.flet]`).

```powershell
flet build windows
```

The output is placed in the `build/` folder.

## Project structure

```
main.py              Entry point
src/gui.py           User interface (Flet)
src/pdf_processor.py PDF search and compilation (PyMuPDF)
src/config.py        User settings (AppData)
src/licensing.py     Hardware-fingerprint activation (Ed25519)
assets/              Icons and fonts
```

## Data and activation

The app stores its settings and managed PDFs in `%APPDATA%\ProtocolFinder`.
Activation uses a hardware fingerprint signed with a private key that is **not** included in this repository.

## License

Copyright (c) 2026 visor flet. Licensed under PolyForm Strict 1.0.0; see the `LICENSE` file.
