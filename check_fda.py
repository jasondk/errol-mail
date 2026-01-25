#!/usr/bin/env python3
"""
Errol-Mail: Full Disk Access Diagnostic Tool

This script checks if Python has the permissions needed to access
Apple Mail's database, and tells you exactly what to do if not.
"""

import os
import sys
import sqlite3
from pathlib import Path


def get_mail_db_path() -> Path:
    """Get the path to Apple Mail's database."""
    return Path.home() / "Library/Mail/V10/MailData/Envelope Index"


def test_database_access() -> tuple[bool, str]:
    """Test if we can access the Mail database."""
    db_path = get_mail_db_path()

    if not db_path.exists():
        return False, f"Mail database not found at {db_path}\nIs Apple Mail configured with at least one account?"

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute("SELECT COUNT(*) FROM messages")
        count = cursor.fetchone()[0]
        conn.close()
        return True, f"Successfully accessed Mail database ({count:,} messages)"
    except sqlite3.OperationalError as e:
        if "unable to open database" in str(e).lower():
            return False, "Permission denied - Full Disk Access required"
        return False, f"Database error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def find_real_python_binary() -> str:
    """
    Find the actual Python binary, following symlinks and detecting Python.app.

    Homebrew Python on Apple Silicon uses a chain:
      /opt/homebrew/bin/python3 (symlink)
      -> /opt/homebrew/Cellar/python@X.Y/version/bin/python3 (wrapper)
      -> Python.app (the actual binary that needs FDA)
    """
    python_path = sys.executable

    # Follow symlinks to get the real path
    real_path = os.path.realpath(python_path)

    # Check if this is Homebrew Python - look for Python.app
    if "/Cellar/python@" in real_path or "/Cellar/python/" in real_path:
        # Extract version info from path
        # e.g., /opt/homebrew/Cellar/python@3.13/3.13.3/Frameworks/...
        parts = real_path.split("/Cellar/")
        if len(parts) > 1:
            cellar_base = parts[0] + "/Cellar/"
            version_path = parts[1].split("/")[0]  # e.g., "python@3.13"

            # Find Python.app in the Frameworks directory
            import glob
            pattern = f"{cellar_base}{version_path}/*/Frameworks/Python.framework/Versions/*/Resources/Python.app"
            matches = glob.glob(pattern)
            if matches:
                # Return the Python.app path (newest version)
                python_app = sorted(matches)[-1]
                return python_app

    # For pyenv or other setups, check if there's a framework
    if "Python.framework" in real_path:
        # Find the Resources/Python.app relative to this
        framework_idx = real_path.find("Python.framework")
        framework_base = real_path[:framework_idx + len("Python.framework")]
        # Try to find Python.app
        for version_dir in Path(framework_base).glob("Versions/*/Resources/Python.app"):
            return str(version_dir)

    return real_path


def get_fda_instructions(python_path: str) -> str:
    """Generate instructions for adding Python to Full Disk Access."""

    # Check if it's a Python.app bundle
    is_app_bundle = python_path.endswith(".app")

    instructions = f"""
To fix this, add Python to Full Disk Access:

1. Open System Settings → Privacy & Security → Full Disk Access

2. Click the + button

3. Press Cmd+Shift+G and paste this path:
   {python_path}

4. Select {"Python.app" if is_app_bundle else "the Python binary"} and click Open

5. Make sure the checkbox is ENABLED

6. Restart your MCP client (Claude Desktop, Claude Code, etc.)
"""

    if is_app_bundle:
        instructions += """
Note: You're using Homebrew Python. The path above is Python.app,
which is the actual binary that needs Full Disk Access (not the
symlinks in /opt/homebrew/bin/).
"""

    return instructions


def print_header():
    print("=" * 60)
    print("  Errol-Mail: Full Disk Access Diagnostic")
    print("=" * 60)
    print()


def main():
    print_header()

    # Show Python info
    print(f"Python executable: {sys.executable}")
    real_binary = find_real_python_binary()
    if real_binary != sys.executable:
        print(f"Actual binary:     {real_binary}")
    print()

    # Test database access
    print("Testing Mail database access...")
    success, message = test_database_access()
    print()

    if success:
        print("✅ " + message)
        print()
        print("Full Disk Access is working! Errol can access your email.")
        return 0
    else:
        print("❌ " + message)
        print()
        print(get_fda_instructions(real_binary))

        # Also provide a quick test command
        print("-" * 60)
        print("After granting access, run this script again to verify:")
        print(f"  python3 {__file__}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
