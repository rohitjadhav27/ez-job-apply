"""Print a bcrypt hash for PASSWORD_HASH_<PERSON>. Prompts for the password (never pass it as an argument)."""
import getpass
import sys

import bcrypt


def main() -> None:
    password = getpass.getpass("Password: ")
    if not password:
        sys.exit("Empty password")
    raw = password.encode()
    if len(raw) > 72:
        sys.exit(f"Password is {len(raw)} bytes; bcrypt only uses the first 72. Choose a shorter one.")
    print(bcrypt.hashpw(raw, bcrypt.gensalt()).decode())


if __name__ == "__main__":
    main()
