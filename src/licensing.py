"""
Activación por huella de hardware (sin login, sin claves que el usuario escriba).

Flujo:
    1. `licensing_tools/generate_keys.py` genera un par de claves Ed25519 (se corre
       UNA sola vez, en la PC del vendedor). La clave privada NUNCA viaja con la app;
       la clave pública se pega en PUBLIC_KEY_B64 más abajo y se compila dentro del .exe.
    2. Al instalar en la PC de un cliente, si no hay licencia válida, la app escribe
       silenciosamente su huella de hardware en un archivo (`hardware_id.txt`) dentro
       de su carpeta de licencia en AppData, y la muestra en pantalla para copiarla.
    3. El vendedor lleva esa huella a su propia PC y corre
       `licensing_tools/generate_license.py` para firmarla con la clave privada,
       generando `license.lic`.
    4. El vendedor copia `license.lic` a la misma carpeta de licencia en la PC del
       cliente. Desde ese momento la app arranca normalmente: en cada inicio vuelve a
       calcular la huella actual y verifica la firma con la clave pública, sin
       necesidad de internet ni de ningún dato escrito por el usuario.
"""

import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.config import get_app_data_dir

# Pegar aquí la clave pública (base64) impresa por licensing_tools/generate_keys.py.
# Mientras esté vacía, la app se considera "sin sistema de licencias" y arranca libre
# (útil en desarrollo, antes de generar el primer par de claves).
PUBLIC_KEY_B64 = "5UrMXX/OQm0zB5B6Cq6he53AxWWLKSjpLpbpX0e+wt4="

LICENSE_FOLDER_NAME = "licencia"
HARDWARE_ID_FILENAME = "hardware_id.txt"
LICENSE_FILENAME = "license.lic"


def get_license_dir() -> str:
    path = os.path.join(get_app_data_dir(), LICENSE_FOLDER_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def get_hardware_id_path() -> str:
    return os.path.join(get_license_dir(), HARDWARE_ID_FILENAME)


def get_license_path() -> str:
    return os.path.join(get_license_dir(), LICENSE_FILENAME)


def _windows_machine_guid() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value)
    except OSError:
        return ""


def _system_volume_serial() -> str:
    try:
        result = subprocess.run(
            ["cmd", "/c", "vol", "C:"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def get_hardware_id() -> str:
    """Huella estable del equipo (no cambia entre reinicios ni reinstalaciones de la
    app, sí cambia si se formatea o se pasa a otra PC)."""
    parts = [
        _windows_machine_guid(),
        _system_volume_serial(),
        platform.node(),
    ]
    raw = "|".join(p for p in parts if p)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def export_hardware_id_file() -> str:
    """Escribe la huella de este equipo en AppData y devuelve la ruta del archivo,
    para que el vendedor la copie/lea al instalar."""
    hardware_id = get_hardware_id()
    path = get_hardware_id_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(hardware_id)
    return path


def check_license() -> tuple[bool, str]:
    """Verifica la licencia de este equipo.

    Devuelve (True, huella) si es válida, o (False, mensaje) si falta, está
    corrupta, no corresponde a este equipo o su firma no es válida.
    """
    if not PUBLIC_KEY_B64:
        # Sin clave pública configurada todavía: no hay sistema de licencias activo.
        return True, get_hardware_id()

    hardware_id = get_hardware_id()
    license_path = get_license_path()

    if not os.path.exists(license_path):
        export_hardware_id_file()
        return False, hardware_id

    try:
        with open(license_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        licensed_hardware_id = data["hardware_id"]
        signature = base64.b64decode(data["signature"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        export_hardware_id_file()
        return False, hardware_id

    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
        public_key.verify(signature, licensed_hardware_id.encode("utf-8"))
    except (InvalidSignature, ValueError):
        export_hardware_id_file()
        return False, hardware_id

    if licensed_hardware_id != hardware_id:
        export_hardware_id_file()
        return False, hardware_id

    return True, hardware_id


def install_license_file(source_path: str) -> tuple[bool, str]:
    """Instala un archivo de licencia elegido por el usuario (botón "buscar
    archivo" o arrastrado a la zona de carga en la pantalla de activación).

    Solo copia el archivo a la carpeta de licencia si se llama exactamente
    LICENSE_FILENAME; cualquier otro nombre se rechaza sin moverlo a ningún
    lado. Tras copiarlo, valida la firma con check_license() y si no es
    válida borra la copia para no dejar un license.lic corrupto instalado.

    Devuelve (True, mensaje de éxito) o (False, mensaje de error).
    """
    filename = os.path.basename(source_path)
    if filename != LICENSE_FILENAME:
        return False, f'Archivo no válido: se esperaba "{LICENSE_FILENAME}".'

    dest_path = get_license_path()
    try:
        shutil.copyfile(source_path, dest_path)
    except OSError as ex:
        return False, f"No se pudo copiar el archivo: {ex}"

    is_valid, _ = check_license()
    if not is_valid:
        try:
            os.remove(dest_path)
        except OSError:
            pass
        return False, "El archivo no corresponde a este equipo o no es válido."

    return True, "Licencia activada correctamente."
