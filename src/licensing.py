"""
Hardware-fingerprint activation (no login, no keys for the user to type).

Flow:
    1. `licensing_tools/generate_keys.py` generates an Ed25519 key pair (run it
       ONCE, on the seller's PC). The private key NEVER ships with the app;
       the public key is pasted into PUBLIC_KEY_B64 below and compiled into the .exe.
    2. When installed on a customer's PC with no valid license, the app silently
       writes its hardware fingerprint to a file (`hardware_id.txt`) inside its
       license folder in AppData, and shows it on screen so it can be copied.
    3. The seller takes that fingerprint to their own PC and runs
       `licensing_tools/generate_license.py` to sign it with the private key,
       producing `license.lic`.
    4. The seller copies `license.lic` into the same license folder on the
       customer's PC. From then on the app starts normally: on every launch it
       recomputes the current fingerprint and verifies the signature with the
       public key, with no internet connection and no user-entered data needed.
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

# Paste the public key (base64) printed by licensing_tools/generate_keys.py here.
# While empty, the app is considered to have "no licensing system" and starts freely
# (useful in development, before the first key pair is generated).
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
    """Stable machine fingerprint (does not change across reboots or app
    reinstalls; it does change if the machine is reformatted or the app moves to another PC)."""
    parts = [
        _windows_machine_guid(),
        _system_volume_serial(),
        platform.node(),
    ]
    raw = "|".join(p for p in parts if p)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def export_hardware_id_file() -> str:
    """Write this machine's fingerprint to AppData and return the file path,
    so the seller can copy/read it during installation."""
    hardware_id = get_hardware_id()
    path = get_hardware_id_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(hardware_id)
    return path


def check_license() -> tuple[bool, str]:
    """Verify this machine's license.

    Returns (True, fingerprint) if valid, or (False, message) if it is missing,
    corrupt, belongs to a different machine, or has an invalid signature.
    """
    if not PUBLIC_KEY_B64:
        # No public key configured yet: the licensing system is not active.
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
    """Install a license file chosen by the user (via the "browse file" button
    or dragged onto the drop zone on the activation screen).

    Only copies the file into the license folder if it is named exactly
    LICENSE_FILENAME; any other name is rejected without moving it anywhere.
    After copying, validates the signature with check_license() and, if it is
    not valid, deletes the copy so no corrupt license.lic is left installed.

    Returns (True, success message) or (False, error message).
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
