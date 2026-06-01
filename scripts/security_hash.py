from __future__ import annotations

import argparse
import getpass

from app.services.security import generate_encryption_key, hash_secret


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a SmartLens PBKDF2 secret hash.")
    parser.add_argument("secret", nargs="?", help="Secret to hash. If omitted, you will be prompted.")
    parser.add_argument("--encryption-key", action="store_true", help="Generate a Fernet key for SECRET_ENCRYPTION_KEY.")
    args = parser.parse_args()

    if args.encryption_key:
        print(generate_encryption_key())
        return

    secret = args.secret or getpass.getpass("Secret: ")
    print(hash_secret(secret))


if __name__ == "__main__":
    main()
