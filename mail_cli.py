#!/usr/bin/env python3
"""
Apple Mail CLI - Command line interface for Apple Mail database access.

Usage:
    python mail_cli.py mailboxes              # List all mailboxes
    python mail_cli.py find Projects          # Find mailboxes by name
    python mail_cli.py recent                 # List recent messages
    python mail_cli.py folder "Projects"      # List messages in folder
    python mail_cli.py unread                 # List unread messages
    python mail_cli.py search --subject "test" --days 7

Requires Full Disk Access permission in System Settings.
"""

import argparse
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from database import MailDatabase, MailDatabaseError, format_flag, get_flag_names, FLAG_EMOJIS
from messages import MessageQuery


def cmd_mailboxes(args):
    """List all mailboxes"""
    db = MailDatabase()
    mailboxes = db.get_mailboxes()

    print(f"\nMailboxes ({len(mailboxes)}):")
    print("=" * 70)

    for mb in mailboxes:
        unread = f" [{mb['unread_count']} unread]" if mb.get('unread_count') else ""
        total = f" ({mb['total_count']} msgs)" if mb.get('total_count') else ""
        print(f"  {mb['folder_path'] or '(root)'}{total}{unread}")

    return 0


def cmd_find(args):
    """Find mailboxes by name"""
    db = MailDatabase()
    matches = db.find_mailbox(args.search)

    if not matches:
        print(f"\nNo mailboxes found matching '{args.search}'")
        return 1

    print(f"\nMailboxes matching '{args.search}' ({len(matches)}):")
    print("=" * 70)

    for mb in matches:
        print(f"\n  ID: {mb['id']}")
        print(f"  Path: {mb['folder_path']}")
        print(f"  Protocol: {mb['protocol']}")
        print(f"  Total: {mb['total_count']}, Unread: {mb['unread_count']}")

    return 0


def cmd_recent(args):
    """List recent messages"""
    query = MessageQuery()
    messages = query.get_recent_messages(limit=args.limit)

    print(f"\nRecent Messages ({len(messages)}):")
    print("=" * 70)

    for msg in messages:
        _print_message(msg)

    return 0


def cmd_folder(args):
    """List messages in a folder"""
    query = MessageQuery()
    messages = query.get_messages_by_folder(
        args.folder,
        limit=args.limit,
        include_read=not args.unread_only
    )

    if not messages:
        print(f"\nNo messages found in folder '{args.folder}'")
        print("(The folder may not exist or may be empty)")
        return 1

    print(f"\nMessages in '{args.folder}' ({len(messages)}):")
    print("=" * 70)

    for msg in messages:
        _print_message(msg)

    return 0


def cmd_unread(args):
    """List unread messages"""
    query = MessageQuery()
    messages = query.get_recent_messages(
        limit=args.limit,
        include_read=False
    )

    if not messages:
        print("\nNo unread messages!")
        return 0

    print(f"\nUnread Messages ({len(messages)}):")
    print("=" * 70)

    for msg in messages:
        _print_message(msg)

    return 0


def cmd_search(args):
    """Search messages"""
    query = MessageQuery()
    messages = query.search_messages(
        subject_contains=args.subject,
        sender_contains=args.sender,
        days_back=args.days,
        limit=args.limit
    )

    if not messages:
        print("\nNo messages found matching criteria")
        return 1

    print(f"\nSearch Results ({len(messages)}):")
    print("=" * 70)

    for msg in messages:
        _print_message(msg)

    return 0


def cmd_flagged(args):
    """List flagged messages"""
    db = MailDatabase()
    flag_names = get_flag_names()

    # Map color names to flag_color values
    color_map = {
        "red": 1, "orange": 2, "yellow": 3, "green": 4,
        "blue": 5, "purple": 6, "gray": 7, "grey": 7
    }

    # Determine which flag color to filter by
    flag_color_filter = None
    if args.color:
        color_lower = args.color.lower()
        if color_lower in color_map:
            flag_color_filter = color_map[color_lower]
        else:
            # Try to match by label name
            for fc, name in flag_names.items():
                if color_lower in name.lower():
                    flag_color_filter = fc
                    break

        if flag_color_filter is None:
            print(f"\nUnknown flag color: {args.color}")
            print("Available colors: red, orange, yellow, green, blue, purple, gray")
            print("Or use your custom labels:")
            for fc in range(1, 8):
                emoji = FLAG_EMOJIS.get(fc, "")
                name = flag_names.get(fc, f"Flag {fc}")
                print(f"  {emoji} {name}")
            return 1

    # Query flagged messages
    with db.connection() as conn:
        query = """
            SELECT
                m.ROWID as message_id,
                m.mailbox,
                m.subject_prefix,
                subj.subject as subject_text,
                m.date_received,
                m.read,
                m.flagged,
                m.flag_color,
                sender_addr.address as sender_email,
                sender_addr.comment as sender_name,
                mb.url as mailbox_url
            FROM messages m
            LEFT JOIN subjects subj ON m.subject = subj.ROWID
            LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
            LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
            WHERE m.flagged = 1
        """
        params = []

        if flag_color_filter:
            query += " AND m.flag_color = ?"
            params.append(flag_color_filter)

        if args.folder:
            query += " AND mb.url LIKE ?"
            params.append(f"%{args.folder}%")

        query += " ORDER BY m.date_received DESC LIMIT ?"
        params.append(args.limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        if flag_color_filter:
            label = format_flag(flag_color_filter)
            print(f"\nNo {label} flagged messages found")
        else:
            print("\nNo flagged messages found")
        return 0

    # Header
    if flag_color_filter:
        label = format_flag(flag_color_filter)
        print(f"\nFlagged Messages - {label} ({len(rows)}):")
    else:
        print(f"\nAll Flagged Messages ({len(rows)}):")
    print("=" * 70)

    # Print messages
    from datetime import datetime
    for row in rows:
        subject = (row["subject_prefix"] or "") + (row["subject_text"] or "")
        sender = row["sender_name"] or row["sender_email"] or "Unknown"

        msg = {
            "message_id": row["message_id"],
            "subject": subject,
            "date_received": datetime.fromtimestamp(row["date_received"]).isoformat() if row["date_received"] else None,
            "is_read": bool(row["read"]),
            "is_flagged": bool(row["flagged"]),
            "flag_color": row["flag_color"],
            "from": sender,
        }
        _print_message(msg, show_flag_detail=True)

    return 0


def cmd_flags(args):
    """Show flag color definitions"""
    flag_names = get_flag_names()

    print("\nYour Flag Colors:")
    print("=" * 40)

    for fc in range(1, 8):
        emoji = FLAG_EMOJIS.get(fc, "")
        name = flag_names.get(fc, f"Flag {fc}")
        print(f"  {emoji} {name}")

    print("\nSource: ~/Library/Containers/com.apple.mail/")
    print("        Data/Library/Preferences/com.apple.mail.plist")

    return 0


def cmd_tables(args):
    """List database tables (debug)"""
    db = MailDatabase()
    tables = db.get_tables()

    print(f"\nDatabase Tables ({len(tables)}):")
    print("=" * 40)

    for table in tables:
        print(f"  - {table}")

    return 0


def cmd_schema(args):
    """Show table schema (debug)"""
    db = MailDatabase()
    schema = db.get_table_schema(args.table)

    print(f"\nSchema for '{args.table}':")
    print("=" * 60)

    for col in schema:
        pk = " [PK]" if col['pk'] else ""
        nullable = " NOT NULL" if col['notnull'] else ""
        print(f"  {col['name']:30} {col['type']:15}{pk}{nullable}")

    return 0


def _print_message(msg, show_flag_detail: bool = False):
    """Pretty-print a message"""
    read_marker = "  " if msg.get("is_read", True) else "* "

    # Flag display
    flag_color = msg.get("flag_color")
    if msg.get("is_flagged") and flag_color:
        flag_marker = FLAG_EMOJIS.get(flag_color, "🚩")
    elif msg.get("is_flagged"):
        flag_marker = "🚩"
    else:
        flag_marker = " "

    date = msg.get("date_received", "")[:16] if msg.get("date_received") else "Unknown date"
    sender = str(msg.get("from") or "Unknown sender")
    subject = str(msg.get("subject") or "(No subject)")

    # Truncate long fields
    if len(sender) > 40:
        sender = sender[:37] + "..."
    if len(subject) > 55:
        subject = subject[:52] + "..."

    print(f"\n{read_marker}{flag_marker} {date}")
    print(f"     From: {sender}")
    print(f"     Subject: {subject}")

    # Show flag label if requested
    if show_flag_detail and msg.get("is_flagged") and flag_color:
        flag_label = format_flag(flag_color, include_emoji=False)
        print(f"     Flag: {flag_label}")


def main():
    parser = argparse.ArgumentParser(
        description="Apple Mail CLI - Access Mail database from command line",
        epilog="Requires Full Disk Access in System Settings → Privacy & Security"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # mailboxes command
    p_mailboxes = subparsers.add_parser("mailboxes", help="List all mailboxes/folders")
    p_mailboxes.set_defaults(func=cmd_mailboxes)

    # find command
    p_find = subparsers.add_parser("find", help="Find mailboxes by name")
    p_find.add_argument("search", help="Search term for mailbox name")
    p_find.set_defaults(func=cmd_find)

    # recent command
    p_recent = subparsers.add_parser("recent", help="List recent messages")
    p_recent.add_argument("--limit", type=int, default=10, help="Max messages to show")
    p_recent.set_defaults(func=cmd_recent)

    # folder command
    p_folder = subparsers.add_parser("folder", help="List messages in a folder")
    p_folder.add_argument("folder", help="Folder name to search for")
    p_folder.add_argument("--limit", type=int, default=10, help="Max messages to show")
    p_folder.add_argument("--unread-only", action="store_true", help="Only show unread")
    p_folder.set_defaults(func=cmd_folder)

    # unread command
    p_unread = subparsers.add_parser("unread", help="List unread messages")
    p_unread.add_argument("--limit", type=int, default=10, help="Max messages to show")
    p_unread.set_defaults(func=cmd_unread)

    # search command
    p_search = subparsers.add_parser("search", help="Search messages")
    p_search.add_argument("--subject", help="Subject contains")
    p_search.add_argument("--sender", help="Sender contains")
    p_search.add_argument("--days", type=int, help="Last N days only")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")
    p_search.set_defaults(func=cmd_search)

    # flagged command
    p_flagged = subparsers.add_parser("flagged", help="List flagged messages")
    p_flagged.add_argument("--color", help="Filter by flag color (red, blue, etc.) or label name")
    p_flagged.add_argument("--folder", help="Filter by folder name")
    p_flagged.add_argument("--limit", type=int, default=20, help="Max messages to show")
    p_flagged.set_defaults(func=cmd_flagged)

    # flags command (show flag definitions)
    p_flags = subparsers.add_parser("flags", help="Show your flag color definitions")
    p_flags.set_defaults(func=cmd_flags)

    # Debug commands
    p_tables = subparsers.add_parser("tables", help="List database tables (debug)")
    p_tables.set_defaults(func=cmd_tables)

    p_schema = subparsers.add_parser("schema", help="Show table schema (debug)")
    p_schema.add_argument("table", help="Table name")
    p_schema.set_defaults(func=cmd_schema)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except MailDatabaseError as e:
        print(f"\nError: {e}")
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
