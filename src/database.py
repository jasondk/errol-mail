#!/usr/bin/env python3
"""
Apple Mail Database Access

Provides read-only access to the macOS Mail SQLite database.
Requires Full Disk Access permission in System Settings.
"""

import sqlite3
import subprocess
import plistlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager


# Mail database paths (V10 = macOS 12+)
MAIL_V10_PATH = Path.home() / "Library/Mail/V10"
MAIL_DB_PATH = MAIL_V10_PATH / "MailData/Envelope Index"

# Default flag colors (flag_color value -> color name)
DEFAULT_FLAG_COLORS = {
    0: "",
    1: "Red",
    2: "Orange",
    3: "Yellow",
    4: "Green",
    5: "Blue",
    6: "Purple",
    7: "Gray",
}

# Flag color emojis
FLAG_EMOJIS = {
    0: "",
    1: "🔴",
    2: "🟠",
    3: "🟡",
    4: "🟢",
    5: "🔵",
    6: "🟣",
    7: "⚪",
}


def get_flag_names() -> Dict[int, str]:
    """
    Get user's custom flag names from Mail preferences.

    Returns:
        Dict mapping flag_color (1-7) to custom name.
        Falls back to default color names if preferences unavailable.
    """
    try:
        # Read from Mail preferences using defaults command
        result = subprocess.run(
            ["defaults", "read", "com.apple.mail", "FlagNames"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            # Parse the plist-style output
            # Format: { 0 = "Name"; 1 = "Name"; ... }
            output = result.stdout.strip()

            # Convert to proper plist format and parse
            # The defaults output is not valid plist, so we parse manually
            names = {}
            for line in output.split('\n'):
                line = line.strip().rstrip(';')
                if '=' in line:
                    parts = line.split('=', 1)
                    try:
                        # Plist index is 0-based, flag_color is 1-based
                        plist_idx = int(parts[0].strip())
                        name = parts[1].strip().strip('"')
                        # Map plist index to flag_color (add 1)
                        flag_color = plist_idx + 1
                        names[flag_color] = name
                    except (ValueError, IndexError):
                        continue

            # Fill in any missing with defaults
            for fc in range(1, 8):
                if fc not in names:
                    names[fc] = DEFAULT_FLAG_COLORS.get(fc, f"Flag {fc}")

            return names
    except Exception:
        pass

    # Return defaults if we couldn't read preferences
    return DEFAULT_FLAG_COLORS.copy()


def format_flag(flag_color: Optional[int], include_emoji: bool = True) -> str:
    """
    Format a flag color as a display string.

    Args:
        flag_color: The flag_color value (1-7, or None/0 for no flag)
        include_emoji: Whether to include the color emoji

    Returns:
        Formatted string like "🔴 Action needed" or just "Action needed"
    """
    if not flag_color:
        return ""

    names = get_flag_names()
    name = names.get(flag_color, f"Flag {flag_color}")

    if include_emoji:
        emoji = FLAG_EMOJIS.get(flag_color, "🚩")
        return f"{emoji} {name}"

    return name


class MailDatabaseError(Exception):
    """Exception for Mail database access errors"""
    pass


class MailDatabase:
    """Read-only access to Apple Mail's SQLite database"""

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database connection.

        Args:
            db_path: Optional custom path to Envelope Index database
        """
        self.db_path = db_path or MAIL_DB_PATH
        self._validate_database()

    def _validate_database(self):
        """Validate database exists and is accessible"""
        if not self.db_path.exists():
            raise MailDatabaseError(
                f"Mail database not found at: {self.db_path}\n"
                "Please ensure Mail app is configured and has received emails."
            )

    @contextmanager
    def connection(self):
        """
        Context manager for read-only database connection.

        Yields:
            sqlite3.Connection object
        """
        try:
            # Use URI mode for read-only access
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro",
                uri=True,
                timeout=10.0
            )
            conn.row_factory = sqlite3.Row  # Enable column access by name
            yield conn
        except sqlite3.OperationalError as e:
            if "unable to open database file" in str(e):
                raise MailDatabaseError(
                    "Cannot access Mail database. Please grant Full Disk Access:\n"
                    "System Settings → Privacy & Security → Full Disk Access\n"
                    "Add your terminal app or Python interpreter."
                ) from e
            raise
        finally:
            try:
                conn.close()
            except NameError:
                pass  # Connection was never established

    def get_tables(self) -> List[str]:
        """Get list of all tables in the database"""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row[0] for row in cursor.fetchall()]

    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """Get column information for a table"""
        with self.connection() as conn:
            cursor = conn.execute(f"PRAGMA table_info({table_name})")
            return [
                {
                    "cid": row[0],
                    "name": row[1],
                    "type": row[2],
                    "notnull": row[3],
                    "default": row[4],
                    "pk": row[5]
                }
                for row in cursor.fetchall()
            ]

    def get_mailboxes(self) -> List[Dict[str, Any]]:
        """
        Get all mailboxes (folders) from the database.

        Returns:
            List of mailbox dictionaries with id, url, and parsed info
        """
        with self.connection() as conn:
            cursor = conn.execute("""
                SELECT
                    ROWID,
                    url,
                    total_count,
                    unread_count
                FROM mailboxes
                ORDER BY url
            """)

            mailboxes = []
            for row in cursor.fetchall():
                mailbox = {
                    "id": row["ROWID"],
                    "url": row["url"],
                    "total_count": row["total_count"],
                    "unread_count": row["unread_count"],
                }

                # Parse the URL to extract account and folder info
                parsed = self._parse_mailbox_url(row["url"])
                mailbox.update(parsed)

                mailboxes.append(mailbox)

            return mailboxes

    def _parse_mailbox_url(self, url: str) -> Dict[str, str]:
        """
        Parse mailbox URL to extract account and folder info.

        URL formats:
        - imap://ACCOUNT-UUID/INBOX
        - imap://ACCOUNT-UUID/INBOX/Projects
        - ews://ACCOUNT-UUID/INBOX/Subfolder
        """
        import urllib.parse

        result = {
            "protocol": "",
            "account_uuid": "",
            "folder_path": "",
            "folder_name": ""
        }

        if not url:
            return result

        # Extract protocol
        if "://" in url:
            protocol, rest = url.split("://", 1)
            result["protocol"] = protocol

            # Split account UUID from folder path
            parts = rest.split("/", 1)
            result["account_uuid"] = parts[0]

            if len(parts) > 1:
                folder_path = urllib.parse.unquote(parts[1])
                result["folder_path"] = folder_path
                # Get the last folder name
                result["folder_name"] = folder_path.split("/")[-1] if folder_path else ""

        return result

    def get_accounts(self) -> List[Dict[str, Any]]:
        """
        Get unique mail accounts from mailboxes.

        Returns:
            List of account dictionaries with UUID and protocol
        """
        mailboxes = self.get_mailboxes()

        accounts = {}
        for mb in mailboxes:
            uuid = mb.get("account_uuid")
            if uuid and uuid not in accounts:
                accounts[uuid] = {
                    "uuid": uuid,
                    "protocol": mb.get("protocol", ""),
                    "mailboxes": []
                }
            if uuid:
                accounts[uuid]["mailboxes"].append(mb)

        return list(accounts.values())

    def find_mailbox(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Find mailboxes matching a search term.

        Args:
            search_term: Partial folder name to search for (case-insensitive)

        Returns:
            List of matching mailboxes
        """
        mailboxes = self.get_mailboxes()
        search_lower = search_term.lower()

        return [
            mb for mb in mailboxes
            if search_lower in mb.get("folder_path", "").lower()
            or search_lower in mb.get("folder_name", "").lower()
        ]


def main():
    """CLI tool to explore Mail database"""
    import argparse

    parser = argparse.ArgumentParser(description="Explore Apple Mail database")
    parser.add_argument("command", choices=["tables", "mailboxes", "accounts", "schema", "find"],
                        help="Command to run")
    parser.add_argument("--table", help="Table name for schema command")
    parser.add_argument("--search", help="Search term for find command")

    args = parser.parse_args()

    try:
        db = MailDatabase()

        if args.command == "tables":
            tables = db.get_tables()
            print(f"Tables in Mail database ({len(tables)}):\n")
            for table in tables:
                print(f"  - {table}")

        elif args.command == "schema":
            if not args.table:
                print("Error: --table required for schema command")
                return 1
            schema = db.get_table_schema(args.table)
            print(f"Schema for '{args.table}':\n")
            for col in schema:
                print(f"  {col['name']:30} {col['type']:15} {'PK' if col['pk'] else ''}")

        elif args.command == "mailboxes":
            mailboxes = db.get_mailboxes()
            print(f"Mailboxes ({len(mailboxes)}):\n")
            for mb in mailboxes:
                unread = f" ({mb['unread_count']} unread)" if mb.get('unread_count') else ""
                print(f"  [{mb['id']:4}] {mb['folder_path']}{unread}")

        elif args.command == "accounts":
            accounts = db.get_accounts()
            print(f"Mail Accounts ({len(accounts)}):\n")
            for acc in accounts:
                print(f"  {acc['protocol']}://{acc['uuid'][:8]}...")
                print(f"    Mailboxes: {len(acc['mailboxes'])}")

        elif args.command == "find":
            if not args.search:
                print("Error: --search required for find command")
                return 1
            matches = db.find_mailbox(args.search)
            print(f"Mailboxes matching '{args.search}' ({len(matches)}):\n")
            for mb in matches:
                print(f"  [{mb['id']:4}] {mb['folder_path']}")
                print(f"         Total: {mb['total_count']}, Unread: {mb['unread_count']}")

        return 0

    except MailDatabaseError as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
