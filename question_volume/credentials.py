import os
import sys


def credential_store():
    if sys.platform != "win32":
        raise RuntimeError("Credential storage requires Windows.")
    # Explicitly use the Windows vault; never fall back to a plaintext backend.
    from keyring.backends.Windows import WinVaultKeyring

    return WinVaultKeyring()


def api_key():
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    return value or credential_store().get_password("question-volume", "openai-api-key")


def save_api_key(value):
    value = value.strip()
    if not value:
        raise ValueError("The API key cannot be empty.")
    credential_store().set_password("question-volume", "openai-api-key", value)
