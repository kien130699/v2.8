from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_CRYPTOGRAPHY = True
except ImportError:
    Fernet = None
    InvalidToken = Exception
    HAS_CRYPTOGRAPHY = False

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "data" / ".secret.key"


def _get_or_create_key() -> bytes:
    if not HAS_CRYPTOGRAPHY:
        return b""
    env_key = os.getenv("V28_SECRET_KEY", "").strip()
    if env_key:
        digest = hashlib.sha256(env_key.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    if KEY_FILE.exists():
        try:
            content = KEY_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content.encode("utf-8")
        except Exception:
            pass

    # Generate new persistent 32-byte urlsafe Fernet key
    new_key = Fernet.generate_key()
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(new_key.decode("utf-8"), encoding="utf-8")
    except Exception:
        pass
    return new_key


_FERNET = Fernet(_get_or_create_key()) if HAS_CRYPTOGRAPHY else None


def encrypt_token(plain_token: str) -> str:
    if not plain_token:
        return ""
    if plain_token.startswith("enc:"):
        return plain_token
    if _FERNET is None:
        return plain_token
    encrypted_bytes = _FERNET.encrypt(plain_token.encode("utf-8"))
    return f"enc:{encrypted_bytes.decode('utf-8')}"


def decrypt_token(stored_token: str) -> str:
    if not stored_token:
        return ""
    if not stored_token.startswith("enc:"):
        return stored_token
    if _FERNET is None:
        return stored_token
    raw_payload = stored_token[4:]
    try:
        decrypted_bytes = _FERNET.decrypt(raw_payload.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except (InvalidToken, Exception):
        return stored_token
