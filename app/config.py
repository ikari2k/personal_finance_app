"""Filesystem path configuration for the finance tracker.

Resolves the on-disk locations of the ledger and config files relative to
the project root, and defines the host/port the app binds to. Centralizing
these paths here means storage modules never hardcode a path themselves.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"

LEDGER_PATH = DATA_DIR / "ledger.csv"
ACCOUNTS_PATH = CONFIG_DIR / "accounts.toml"
CATEGORIES_PATH = CONFIG_DIR / "categories.toml"
RULES_PATH = CONFIG_DIR / "rules.toml"
IMPORT_MAPPINGS_DIR = CONFIG_DIR / "import_mappings"

# The app is single-user and local-only; it must never bind beyond loopback.
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
