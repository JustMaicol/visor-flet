# Protocol Finder

Buscador y compilador de páginas de escrituras en documentos PDF. Aplicación de escritorio hecha con [Flet](https://flet.dev) (Python) y PyMuPDF.

## Requisitos

- Windows 10/11
- Python 3.12 o superior (desarrollado con 3.14)
- Git

## Instalación

```powershell
git clone <URL-DEL-REPOSITORIO>
cd visor-flet

python -m venv venv
.\venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## Ejecución

```powershell
python main.py
```

## Empaquetar como .exe

La configuración del producto está en `pyproject.toml` (`[tool.flet]`).

```powershell
flet build windows
```

El resultado queda en la carpeta `build/`.

## Estructura

```
main.py              Punto de entrada
src/gui.py           Interfaz (Flet)
src/pdf_processor.py Búsqueda y compilación de PDFs (PyMuPDF)
src/config.py        Configuración del usuario (AppData)
src/licensing.py     Activación por huella de hardware (Ed25519)
assets/              Iconos y fuentes
```

## Datos y licencia de uso

La app guarda su configuración y los PDFs administrados en `%APPDATA%\ProtocolFinder`.
La activación usa una huella de hardware firmada con una clave privada que **no** está en este repositorio.

## Licencia

Copyright (c) 2026 visor flet. Licenciado bajo PolyForm Strict 1.0.0; ver el archivo `LICENSE`.
