import json
import os
from dataclasses import dataclass

APP_NAME = "ProtocolFinder"
VALID_THEME_MODES = {"Dark", "Light", "System"}


def get_app_data_dir() -> str:
    """App data folder in AppData (falls back to the home directory if APPDATA is not set)."""
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    app_dir = os.path.join(base, APP_NAME)
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def get_pdf_storage_dir() -> str:
    """Folder where the PDFs managed by the app are copied/moved."""
    storage_dir = os.path.join(get_app_data_dir(), "pdfs")
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


def _config_file_path() -> str:
    return os.path.join(get_app_data_dir(), "config.json")


def _validate_int(raw: dict, key: str, default: int, warnings: list[str], min_value: int | None = None) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        warnings.append(f"'{key}' debía ser un número entero; se usó el valor por defecto ({default}).")
        return default
    if min_value is not None and value < min_value:
        warnings.append(f"'{key}' debía ser mayor o igual a {min_value}; se usó el valor por defecto ({default}).")
        return default
    return value


def _validate_str(raw: dict, key: str, default: str, warnings: list[str]) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        warnings.append(f"'{key}' debía ser texto; se usó el valor por defecto.")
        return default
    return value


@dataclass
class AppConfig:
    crop_x0: int = 0
    crop_y0: int = 0
    crop_width: int = 250
    crop_height: int = 150
    last_directory: str = ""
    theme_mode: str = "Dark"

    def to_dict(self) -> dict:
        return {
            "crop_x0": self.crop_x0,
            "crop_y0": self.crop_y0,
            "crop_width": self.crop_width,
            "crop_height": self.crop_height,
            "last_directory": self.last_directory,
            "theme_mode": self.theme_mode,
        }

    @classmethod
    def load(cls) -> tuple["AppConfig", list[str]]:
        """Load the config from AppData. Never raises: on any problem it falls back to
        default values and reports them in the returned warnings list, so the UI
        layer decides how to display them (the config knows nothing about Flet)."""
        instance = cls()
        warnings: list[str] = []
        path = _config_file_path()

        if not os.path.exists(path):
            return instance, warnings

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"No se pudo leer la configuración guardada, se usarán valores por defecto ({e}).")
            return instance, warnings

        if not isinstance(raw, dict):
            warnings.append("El archivo de configuración tiene un formato inválido; se usarán valores por defecto.")
            return instance, warnings

        instance.crop_x0 = _validate_int(raw, "crop_x0", instance.crop_x0, warnings)
        instance.crop_y0 = _validate_int(raw, "crop_y0", instance.crop_y0, warnings)
        instance.crop_width = _validate_int(raw, "crop_width", instance.crop_width, warnings, min_value=1)
        instance.crop_height = _validate_int(raw, "crop_height", instance.crop_height, warnings, min_value=1)
        instance.last_directory = _validate_str(raw, "last_directory", instance.last_directory, warnings)

        theme = _validate_str(raw, "theme_mode", instance.theme_mode, warnings)
        if theme not in VALID_THEME_MODES:
            warnings.append(f"'theme_mode' tenía un valor no soportado ('{theme}'); se usó 'Dark'.")
            theme = "Dark"
        instance.theme_mode = theme

        return instance, warnings

    def save(self) -> str | None:
        """Save the config to AppData. Returns a readable error message, or None on success."""
        try:
            with open(_config_file_path(), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=4, ensure_ascii=False)
            return None
        except OSError as e:
            return f"No se pudo guardar la configuración: {e}"


_instance: AppConfig | None = None
_load_warnings: list[str] = []


def get_config() -> AppConfig:
    """Global config instance, loaded lazily (no I/O when this module is imported)."""
    global _instance, _load_warnings
    if _instance is None:
        _instance, _load_warnings = AppConfig.load()
    return _instance


def pop_load_warnings() -> list[str]:
    """Return and clear the warnings generated while loading the config, so the UI shows them only once."""
    global _load_warnings
    warnings, _load_warnings = _load_warnings, []
    return warnings
