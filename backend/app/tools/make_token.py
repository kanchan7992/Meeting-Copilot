"""Creates a client access token. Prints the token once (for the desktop app) and its hash (for .env).

Usage: python -m app.tools.make_token
"""

from __future__ import annotations

import hashlib
import secrets


# Generates a random token and prints it with its SHA-256 hash. — pradeep.singh@rodicconsultants.com
def main() -> None:
    token = secrets.token_urlsafe(32)
    print("Paste into the desktop app Settings (shown once, not stored anywhere):")
    print(f"  {token}")
    print("Add to backend/.env (comma-separate several):")
    print(f"  APP_AUTH_TOKEN_SHA256={hashlib.sha256(token.encode()).hexdigest()}")


if __name__ == "__main__":
    main()
