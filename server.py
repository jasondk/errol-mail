#!/usr/bin/env python3
"""
Apple Mail MCP Server

Exposes Apple Mail database access via the Model Context Protocol.
Allows AI assistants to browse, search, and read emails from the local Mail database.

Usage:
    python server.py                    # Run MCP server (stdio)
    python server.py --test             # Test mode - run CLI commands

Requires Full Disk Access permission in System Settings.
"""

import sys
from pathlib import Path
from typing import Optional
from datetime import datetime

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp.server.fastmcp import FastMCP
from database import (
    MailDatabase,
    MailDatabaseError,
    get_flag_names,
    format_flag,
    FLAG_EMOJIS,
    DEFAULT_FLAG_COLORS
)
from messages import MessageQuery
from email_reader import parse_emlx_file, get_emlx_flag_color
from threads import ThreadQuery
from attachments import (
    list_attachments as list_email_attachments,
    extract_attachment,
    extract_all_attachments,
    cleanup_extracted_attachments
)
from applescript_helper import (
    check_message_availability,
    trigger_message_download as trigger_download,
    trigger_message_download_silent as trigger_download_silent,
    open_message_in_mail as open_in_mail,
    set_message_flag,
    clear_message_flag,
    set_message_read_status,
    minimize_mail,
    close_all_message_windows
)


# Create the MCP server
mcp = FastMCP("apple-mail")


# ============================================================================
# MAILBOX TOOLS
# ============================================================================

@mcp.tool()
def list_mailboxes() -> str:
    """
    List all mailboxes (folders) in Apple Mail.

    Returns a list of all mail folders across all accounts, with message counts.
    Useful for discovering what folders exist before querying messages.
    """
    try:
        db = MailDatabase()
        mailboxes = db.get_mailboxes()

        lines = [f"# Mailboxes ({len(mailboxes)} total)\n"]

        for mb in mailboxes:
            unread = f" [{mb['unread_count']} unread]" if mb.get('unread_count') else ""
            total = f" ({mb['total_count']} msgs)" if mb.get('total_count') else ""
            path = mb['folder_path'] or '(root)'
            lines.append(f"- {path}{total}{unread}")

        return "\n".join(lines)

    except MailDatabaseError as e:
        return f"Error: {e}"


@mcp.tool()
def find_mailbox(search_term: str) -> str:
    """
    Find mailboxes by name.

    Args:
        search_term: Partial name to search for (case-insensitive)

    Returns detailed info about matching mailboxes including ID, path, and counts.
    """
    try:
        db = MailDatabase()
        matches = db.find_mailbox(search_term)

        if not matches:
            return f"No mailboxes found matching '{search_term}'"

        lines = [f"# Mailboxes matching '{search_term}' ({len(matches)} found)\n"]

        for mb in matches:
            lines.append(f"## {mb['folder_path']}")
            lines.append(f"- **ID**: {mb['id']}")
            lines.append(f"- **Protocol**: {mb['protocol']}")
            lines.append(f"- **Total messages**: {mb['total_count']}")
            lines.append(f"- **Unread**: {mb['unread_count']}")
            lines.append("")

        return "\n".join(lines)

    except MailDatabaseError as e:
        return f"Error: {e}"


# ============================================================================
# MESSAGE TOOLS
# ============================================================================

@mcp.tool()
def get_recent_messages(limit: int = 20, include_read: bool = True) -> str:
    """
    Get recent messages from all mailboxes.

    Args:
        limit: Maximum number of messages to return (default: 20)
        include_read: Whether to include read messages (default: True)

    Returns recent messages sorted by date received, newest first.
    """
    try:
        query = MessageQuery()
        messages = query.get_recent_messages(limit=limit, include_read=include_read)

        if not messages:
            return "No messages found"

        return _format_messages(messages, f"Recent Messages ({len(messages)})")

    except MailDatabaseError as e:
        return f"Error: {e}"


@mcp.tool()
def get_unread_messages(limit: int = 20) -> str:
    """
    Get unread messages from all mailboxes.

    Args:
        limit: Maximum number of messages to return (default: 20)

    Returns unread messages sorted by date received, newest first.
    """
    try:
        query = MessageQuery()
        messages = query.get_recent_messages(limit=limit, include_read=False)

        if not messages:
            return "No unread messages!"

        return _format_messages(messages, f"Unread Messages ({len(messages)})")

    except MailDatabaseError as e:
        return f"Error: {e}"


@mcp.tool()
def get_folder_messages(
    folder_name: str,
    limit: int = 20,
    unread_only: bool = False
) -> str:
    """
    Get messages from a specific folder.

    Args:
        folder_name: Folder name to search for (partial match, e.g. "Projects")
        limit: Maximum number of messages (default: 20)
        unread_only: Only return unread messages (default: False)

    Returns messages from matching folder(s), sorted by date.
    """
    try:
        query = MessageQuery()
        messages = query.get_messages_by_folder(
            folder_name,
            limit=limit,
            include_read=not unread_only
        )

        if not messages:
            return f"No messages found in folder '{folder_name}' (folder may not exist or be empty)"

        return _format_messages(messages, f"Messages in '{folder_name}' ({len(messages)})")

    except MailDatabaseError as e:
        return f"Error: {e}"


@mcp.tool()
def search_messages(
    subject: Optional[str] = None,
    sender: Optional[str] = None,
    days_back: Optional[int] = None,
    limit: int = 50
) -> str:
    """
    Search messages with filters.

    Args:
        subject: Filter by subject (case-insensitive partial match)
        sender: Filter by sender email or name (case-insensitive partial match)
        days_back: Only search messages from the last N days
        limit: Maximum results (default: 50)

    At least one filter should be provided. Returns matching messages sorted by date.
    """
    if not any([subject, sender, days_back]):
        return "Please provide at least one search filter (subject, sender, or days_back)"

    try:
        query = MessageQuery()
        messages = query.search_messages(
            subject_contains=subject,
            sender_contains=sender,
            days_back=days_back,
            limit=limit
        )

        if not messages:
            return "No messages found matching criteria"

        # Build filter description
        filters = []
        if subject:
            filters.append(f"subject contains '{subject}'")
        if sender:
            filters.append(f"sender contains '{sender}'")
        if days_back:
            filters.append(f"last {days_back} days")
        filter_desc = ", ".join(filters)

        return _format_messages(messages, f"Search Results ({len(messages)}) - {filter_desc}")

    except MailDatabaseError as e:
        return f"Error: {e}"


# ============================================================================
# FLAG TOOLS
# ============================================================================

@mcp.tool()
def get_flagged_messages(
    color: Optional[str] = None,
    folder: Optional[str] = None,
    limit: int = 20
) -> str:
    """
    Get flagged messages, optionally filtered by flag color or folder.

    Args:
        color: Filter by flag color (red, orange, yellow, green, blue, purple, gray)
               or by your custom label name
        folder: Filter by folder name (partial match)
        limit: Maximum messages (default: 20)

    Returns flagged messages with their flag color/label shown.
    """
    try:
        db = MailDatabase()
        mq = MessageQuery(db)
        flag_names = get_flag_names()

        # Map color names to flag_color values (1-7 range)
        color_map = {
            "red": 1, "orange": 2, "yellow": 3, "green": 4,
            "blue": 5, "purple": 6, "gray": 7, "grey": 7
        }

        flag_color_filter = None
        if color:
            color_lower = color.lower()
            if color_lower in color_map:
                flag_color_filter = color_map[color_lower]
            else:
                # Try to match by label name
                for fc, name in flag_names.items():
                    if color_lower in name.lower():
                        flag_color_filter = fc
                        break

            if flag_color_filter is None:
                labels = ", ".join([f"{FLAG_EMOJIS.get(fc, '')} {name}" for fc, name in flag_names.items()])
                return f"Unknown flag color: {color}\n\nAvailable: {labels}"

        # Query ALL flagged messages - we'll determine actual color from emlx files
        # The database flag_color is unreliable, and server_messages only has ~20% of messages
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
                    sm.flag_color as server_flag_color,
                    sender_addr.address as sender_email,
                    sender_addr.comment as sender_name,
                    mb.url as mailbox_url
                FROM messages m
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN server_messages sm ON sm.message = m.ROWID
                WHERE m.flagged = 1
            """
            params = []

            if folder:
                query += " AND mb.url LIKE ?"
                params.append(f"%{folder}%")

            query += " ORDER BY m.date_received DESC"

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            return "No flagged messages found"

        # Process rows and determine actual flag color from emlx files
        processed_rows = []
        color_counts = {}

        for row in rows:
            msg_id = row["message_id"]
            mailbox_url = row["mailbox_url"]
            server_color = row["server_flag_color"]

            # Determine actual flag color:
            # 1. Try server_messages.flag_color (if available) - uses 0-6 range
            # 2. Fall back to reading from emlx file (also 0-6 range)
            if server_color is not None:
                actual_color = server_color + 1  # Convert 0-6 to 1-7
            else:
                # Read color from emlx file metadata
                file_path = mq._build_file_path(msg_id, mailbox_url)
                if file_path:
                    emlx_color = get_emlx_flag_color(file_path)
                    if emlx_color is not None:
                        actual_color = emlx_color + 1  # Convert 0-6 to 1-7
                    else:
                        actual_color = 1  # Default to red if unreadable
                else:
                    actual_color = 1  # Default to red if no file

            # Track color counts
            color_counts[actual_color] = color_counts.get(actual_color, 0) + 1

            # Apply color filter (if specified)
            if flag_color_filter and actual_color != flag_color_filter:
                continue

            processed_rows.append({
                "message_id": msg_id,
                "subject_prefix": row["subject_prefix"],
                "subject_text": row["subject_text"],
                "date_received": row["date_received"],
                "sender_email": row["sender_email"],
                "sender_name": row["sender_name"],
                "flag_color": actual_color,
            })

            # Stop if we have enough
            if len(processed_rows) >= limit:
                break

        if not processed_rows:
            if flag_color_filter:
                label = format_flag(flag_color_filter)
                available = ", ".join([f"{format_flag(c)} ({cnt})" for c, cnt in sorted(color_counts.items())])
                return f"No {label} flagged messages found.\n\nFlags in database: {available}"
            return "No flagged messages found"

        # Format as table
        if flag_color_filter:
            label = format_flag(flag_color_filter)
            total_matching = color_counts.get(flag_color_filter, 0)
            header = f"# Flagged Messages - {label} ({len(processed_rows)} of {total_matching})"
        else:
            header = f"# All Flagged Messages ({len(processed_rows)})"

        lines = [header, ""]
        lines.append("| ID | Flag | Date | From | Subject |")
        lines.append("|---:|------|------|------|---------|")

        for row in processed_rows:
            subject = (row["subject_prefix"] or "") + (row["subject_text"] or "")
            sender = row["sender_name"] or row["sender_email"] or "Unknown"
            flag_emoji = FLAG_EMOJIS.get(row["flag_color"], "🚩")
            msg_id = row["message_id"]

            # Format date
            if row["date_received"]:
                date_str = datetime.fromtimestamp(row["date_received"]).strftime("%Y-%m-%d")
            else:
                date_str = "Unknown"

            # Truncate for table display
            if len(sender) > 30:
                sender = sender[:27] + "..."
            if len(subject) > 40:
                subject = subject[:37] + "..."

            lines.append(f"| {msg_id} | {flag_emoji} | {date_str} | {sender} | {subject} |")

        return "\n".join(lines)

    except MailDatabaseError as e:
        return f"Error: {e}"


@mcp.tool()
def list_flag_colors() -> str:
    """
    Show your custom flag color definitions.

    Returns your Mail.app flag labels and their colors.
    Useful for knowing which flag color to filter by.
    """
    try:
        flag_names = get_flag_names()
        db = MailDatabase()
        mq = MessageQuery(db)

        # Get actual flag colors by reading from emlx files
        # The database flag_color is unreliable, so we read from file metadata
        color_counts = {i: 0 for i in range(1, 8)}

        with db.connection() as conn:
            cursor = conn.execute('''
                SELECT m.ROWID, sm.flag_color, mb.url
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN server_messages sm ON sm.message = m.ROWID
                WHERE m.flagged = 1
            ''')
            rows = cursor.fetchall()

        for row in rows:
            msg_id, server_color, mailbox_url = row

            # Determine actual color from server_messages or emlx file
            if server_color is not None:
                actual_color = server_color + 1  # Convert 0-6 to 1-7
            else:
                file_path = mq._build_file_path(msg_id, mailbox_url)
                if file_path:
                    emlx_color = get_emlx_flag_color(file_path)
                    if emlx_color is not None:
                        actual_color = emlx_color + 1
                    else:
                        actual_color = 1
                else:
                    actual_color = 1

            if 1 <= actual_color <= 7:
                color_counts[actual_color] += 1

        lines = ["# Your Flag Colors\n"]
        lines.append("| Emoji | Color | Your Label | In Database |")
        lines.append("|-------|-------|------------|-------------|")

        for fc in range(1, 8):
            emoji = FLAG_EMOJIS.get(fc, "")
            color = DEFAULT_FLAG_COLORS.get(fc, f"Color {fc}")
            name = flag_names.get(fc, color)
            count = color_counts.get(fc, 0)
            count_str = f"{count} msgs" if count > 0 else "-"
            lines.append(f"| {emoji} | {color} | {name} | {count_str} |")

        lines.append("")
        lines.append("*Use the color name or your custom label when filtering flagged messages*")

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading flag names: {e}"


# ============================================================================
# EMAIL READING TOOLS
# ============================================================================

@mcp.tool()
def read_email(message_id: int, max_body_length: int = 10000) -> str:
    """
    Read the full content of an email by its database message ID.

    Args:
        message_id: The database ROWID of the message (shown in message listings)
        max_body_length: Maximum body length to return (default: 10000 chars)

    Returns the email headers and full body text. Use this to read individual
    emails after finding them via search or folder listing.
    """
    try:
        # Direct database lookup to get mailbox URL
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

            file_path = _find_email_file(message_id, row["mailbox_url"])

        if not file_path:
            return f"Could not locate email file for message {message_id}"

        # Parse the email
        result = parse_emlx_file(file_path, max_body_length=max_body_length)

        if not result["success"]:
            return f"Error reading email: {result.get('error', 'Unknown error')}"

        # Format output
        lines = ["# Email Content\n"]
        lines.append(f"**From:** {result['from']}")
        lines.append(f"**To:** {result['to']}")
        if result.get('cc'):
            lines.append(f"**Cc:** {result['cc']}")
        lines.append(f"**Date:** {result['date']}")
        lines.append(f"**Subject:** {result['subject']}")

        if result.get('attachments'):
            att_list = ", ".join([a['filename'] for a in result['attachments']])
            lines.append(f"**Attachments:** {att_list}")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(result['body_text'])

        if result.get('truncated'):
            lines.append("")
            lines.append(f"*[Body truncated at {max_body_length} characters]*")

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading email: {e}"


@mcp.tool()
def read_thread(
    message_id: int,
    max_body_length: int = 5000,
    strip_quotes: bool = True
) -> str:
    """
    Read an entire email thread/conversation containing the specified message.

    Args:
        message_id: Database ROWID of any message in the thread
        max_body_length: Maximum body length per message (default: 5000)
        strip_quotes: Remove redundant quoted text (default: True, recommended for threads)

    Returns all messages in the conversation in chronological order.
    Great for understanding the full context of an email discussion.
    """
    try:
        thread_query = ThreadQuery()
        result = thread_query.get_thread_by_message_id(
            message_id,
            include_body=True,
            strip_quotes=strip_quotes,
            max_body_length=max_body_length
        )

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        lines = [f"# Thread: {result['subject']}"]
        lines.append(f"*{result['message_count']} messages in conversation*\n")

        for i, msg in enumerate(result["messages"], 1):
            lines.append(f"## [{i}/{result['message_count']}] {msg['from']}")
            lines.append(f"**Date:** {msg['date']}")
            lines.append(f"**Subject:** {msg['subject']}")

            # Status indicators
            status_parts = []
            if not msg.get('is_read'):
                status_parts.append("📬 Unread")
            if msg.get('is_flagged'):
                flag_emoji = FLAG_EMOJIS.get(msg.get('flag_color'), "🚩")
                status_parts.append(f"{flag_emoji} Flagged")
            if status_parts:
                lines.append(f"**Status:** {', '.join(status_parts)}")

            if msg.get('attachments'):
                att_list = ", ".join([a['filename'] for a in msg['attachments']])
                lines.append(f"**Attachments:** {att_list}")

            lines.append("")

            if msg.get('body'):
                lines.append(msg['body'])
            elif msg.get('body_error'):
                lines.append(f"*[Could not read body: {msg['body_error']}]*")
            else:
                lines.append("*[No body content]*")

            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"Error reading thread: {e}"


@mcp.tool()
def get_thread_summary(message_id: int) -> str:
    """
    Get a quick summary of an email thread without full message bodies.

    Args:
        message_id: Database ROWID of any message in the thread

    Returns thread summary including participants, message count, and date range.
    Useful for quickly understanding a conversation before reading the full thread.
    """
    try:
        thread_query = ThreadQuery()

        # First get conversation_id
        conv_id = thread_query.get_conversation_id_for_message(message_id)
        if not conv_id:
            return f"Message {message_id} not found or has no conversation"

        result = thread_query.get_thread_summary(conv_id)

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        lines = [f"# Thread Summary: {result['subject']}\n"]
        lines.append(f"**Messages:** {result['message_count']}")
        lines.append(f"**Unread:** {result['unread_count']}")
        lines.append(f"**Flagged:** {result['flagged_count']}")
        lines.append(f"**First message:** {result['first_message_date']}")
        lines.append(f"**Last message:** {result['last_message_date']}")
        lines.append("")
        lines.append("**Participants:**")
        for p in result['participants']:
            lines.append(f"- {p}")

        lines.append("")
        lines.append(f"*Use `read_thread({message_id})` to read the full conversation*")

        return "\n".join(lines)

    except Exception as e:
        return f"Error getting thread summary: {e}"


# ============================================================================
# ATTACHMENT TOOLS
# ============================================================================

@mcp.tool()
def list_attachments(message_id: int) -> str:
    """
    List all attachments in an email.

    Args:
        message_id: Database ROWID of the message

    Returns a list of attachments with filenames, types, and sizes.
    """
    try:
        # Get file path for this message
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

            file_path = _find_email_file(message_id, row["mailbox_url"])

        if not file_path:
            return f"Could not locate email file for message {message_id}"

        result = list_email_attachments(file_path)

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        if not result["attachments"]:
            return f"No attachments in message {message_id}"

        lines = [f"# Attachments ({result['attachment_count']})\n"]
        lines.append("| Filename | Type | Size |")
        lines.append("|----------|------|------|")

        for att in result["attachments"]:
            size_kb = att['size_bytes'] / 1024
            if size_kb >= 1024:
                size_str = f"{size_kb/1024:.1f} MB"
            else:
                size_str = f"{size_kb:.1f} KB"

            lines.append(f"| {att['filename']} | {att['mime_type']} | {size_str} |")

        lines.append("")
        lines.append(f"*Use `extract_attachment({message_id}, 'filename')` to extract a specific file*")

        return "\n".join(lines)

    except Exception as e:
        return f"Error listing attachments: {e}"


@mcp.tool()
def get_attachment(message_id: int, filename: str) -> str:
    """
    Extract an attachment from an email and save it to a temporary directory.

    Args:
        message_id: Database ROWID of the message
        filename: Exact filename of the attachment to extract

    Returns the path to the extracted file, which can then be read or processed.
    """
    try:
        # Get file path for this message
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

            file_path = _find_email_file(message_id, row["mailbox_url"])

        if not file_path:
            return f"Could not locate email file for message {message_id}"

        result = extract_attachment(file_path, filename)

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        lines = ["# Attachment Extracted\n"]
        lines.append(f"**Filename:** {result['filename']}")
        lines.append(f"**Type:** {result['mime_type']}")
        lines.append(f"**Size:** {result['size_bytes']:,} bytes")
        lines.append(f"**Saved to:** `{result['output_path']}`")
        lines.append("")
        lines.append("You can now read or process this file using the path above.")

        return "\n".join(lines)

    except Exception as e:
        return f"Error extracting attachment: {e}"


@mcp.tool()
def extract_all_message_attachments(message_id: int) -> str:
    """
    Extract all attachments from an email.

    Args:
        message_id: Database ROWID of the message

    Returns paths to all extracted files.
    """
    try:
        # Get file path for this message
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

            file_path = _find_email_file(message_id, row["mailbox_url"])

        if not file_path:
            return f"Could not locate email file for message {message_id}"

        result = extract_all_attachments(file_path)

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        if not result["extracted"]:
            return f"No attachments to extract from message {message_id}"

        lines = [f"# Extracted {result['extracted_count']} Attachment(s)\n"]
        lines.append(f"**Output directory:** `{result['output_dir']}`\n")

        lines.append("| Filename | Type | Size | Path |")
        lines.append("|----------|------|------|------|")

        for att in result["extracted"]:
            size_kb = att['size_bytes'] / 1024
            size_str = f"{size_kb:.1f} KB"
            lines.append(f"| {att['filename']} | {att['mime_type']} | {size_str} | `{att['output_path']}` |")

        if result.get("failed"):
            lines.append("")
            lines.append("**Failed to extract:**")
            for f in result["failed"]:
                lines.append(f"- {f['filename']}: {f['error']}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error extracting attachments: {e}"


@mcp.tool()
def cleanup_attachments(older_than_hours: int = 24) -> str:
    """
    Clean up old extracted attachments to free disk space.

    Args:
        older_than_hours: Delete files older than this (default: 24 hours)

    Returns cleanup summary.
    """
    try:
        result = cleanup_extracted_attachments(older_than_hours)

        if not result["success"]:
            return f"Error: {result.get('error', 'Unknown error')}"

        if result["deleted_count"] == 0:
            return f"No attachments older than {older_than_hours} hours to clean up"

        size_mb = result["deleted_bytes"] / (1024 * 1024)
        return f"Cleaned up {result['deleted_count']} files ({size_mb:.1f} MB freed)"

    except Exception as e:
        return f"Error during cleanup: {e}"


# ============================================================================
# SERVER-SIDE MESSAGE TOOLS
# ============================================================================

@mcp.tool()
def check_email_availability(message_id: int) -> str:
    """
    Check if an email is available locally or only on the mail server.

    Args:
        message_id: Database ROWID of the message

    Returns information about whether the email content can be read locally,
    or if it needs to be downloaded from the server first.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text,
                       sender_addr.address as sender_email
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found in database"

        result = check_message_availability(message_id, row["mailbox_url"])

        lines = [f"# Message Availability Check\n"]
        lines.append(f"**Message ID:** {message_id}")
        lines.append(f"**Subject:** {row['subject_text'] or '(No subject)'}")
        lines.append(f"**From:** {row['sender_email'] or 'Unknown'}")
        lines.append("")

        if result["available_locally"]:
            lines.append("✅ **Status:** Available locally")
            if result.get("is_partial"):
                lines.append("⚠️ *Note: This is a partial download - some content may be missing*")
            lines.append(f"**File:** `{result.get('file_path', 'Unknown')}`")
        else:
            lines.append("📡 **Status:** Server-only (not downloaded)")
            lines.append("")
            lines.append("This message exists in your Mail database but the full content")
            lines.append("has not been downloaded locally. To read this message:")
            lines.append("")
            lines.append(f"1. Use `download_email({message_id})` to trigger download via AppleScript")
            lines.append("2. Or open the message manually in Mail.app")

        return "\n".join(lines)

    except Exception as e:
        return f"Error checking availability: {e}"


@mcp.tool()
def download_email(message_id: int) -> str:
    """
    Trigger Mail.app to download a server-only email.

    Args:
        message_id: Database ROWID of the message

    Uses AppleScript to open the message in Mail, which triggers the download
    from the mail server. After downloading, the message can be read normally.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found in database"

        # Check if already available
        availability = check_message_availability(message_id, row["mailbox_url"])
        if availability["available_locally"] and not availability.get("is_partial"):
            return f"Message {message_id} is already downloaded locally. Use `read_email({message_id})` to read it."

        # Trigger download
        result = trigger_download(message_id, row["mailbox_url"])

        if result["success"]:
            lines = ["# Download Triggered Successfully\n"]
            lines.append(f"**Message ID:** {message_id}")
            lines.append(f"**Subject:** {row['subject_text'] or '(No subject)'}")
            lines.append("")
            lines.append(result["message"])
            if result.get("content_length"):
                lines.append(f"**Content length:** {result['content_length']} characters")
            lines.append("")
            lines.append(f"You can now use `read_email({message_id})` to read the full content.")
            return "\n".join(lines)
        else:
            return f"Failed to download message: {result['message']}"

    except Exception as e:
        return f"Error triggering download: {e}"


@mcp.tool()
def open_email_in_mail(message_id: int) -> str:
    """
    Open an email in Apple Mail's viewer window.

    Args:
        message_id: Database ROWID of the message

    Opens the message in Mail.app for manual viewing. This also triggers
    download if the message was server-only.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found in database"

        result = open_in_mail(message_id, row["mailbox_url"])

        if result["success"]:
            return f"Opened message '{row['subject_text']}' in Mail.app"
        else:
            return f"Failed to open message: {result['message']}"

    except Exception as e:
        return f"Error opening message: {e}"


# ============================================================================
# MESSAGE MODIFICATION TOOLS (Headless)
# ============================================================================

@mcp.tool()
def mark_email_read(message_id: int) -> str:
    """
    Mark an email as read (runs silently in background).

    Args:
        message_id: Database ROWID of the message

    This operation runs headless - no windows open, Mail stays in background.
    Changes sync to IMAP/Exchange server.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

        result = set_message_read_status(message_id, row["mailbox_url"], True)

        if result["success"]:
            return f"✓ Marked as read: {row['subject_text'][:50]}"
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def mark_email_unread(message_id: int) -> str:
    """
    Mark an email as unread (runs silently in background).

    Args:
        message_id: Database ROWID of the message

    This operation runs headless - no windows open, Mail stays in background.
    Changes sync to IMAP/Exchange server.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

        result = set_message_read_status(message_id, row["mailbox_url"], False)

        if result["success"]:
            return f"✓ Marked as unread: {row['subject_text'][:50]}"
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def set_email_flag(message_id: int, color: str) -> str:
    """
    Set a flag on an email (runs silently in background).

    Args:
        message_id: Database ROWID of the message
        color: Flag color (red, orange, yellow, green, blue, purple, gray)

    This operation runs headless - no windows open, Mail stays in background.
    Changes sync to IMAP/Exchange server.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

        result = set_message_flag(message_id, row["mailbox_url"], color)

        if result["success"]:
            color_emoji = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢",
                          "blue": "🔵", "purple": "🟣", "gray": "⚪", "grey": "⚪"}.get(color.lower(), "🚩")
            return f"{color_emoji} Flagged {color}: {row['subject_text'][:50]}"
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def clear_email_flag(message_id: int) -> str:
    """
    Remove the flag from an email (runs silently in background).

    Args:
        message_id: Database ROWID of the message

    This operation runs headless - no windows open, Mail stays in background.
    Changes sync to IMAP/Exchange server.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

        result = clear_message_flag(message_id, row["mailbox_url"])

        if result["success"]:
            return f"✓ Flag cleared: {row['subject_text'][:50]}"
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def download_email_silent(message_id: int) -> str:
    """
    Download a server-only email silently (opens and closes window automatically).

    Args:
        message_id: Database ROWID of the message

    Unlike download_email(), this version closes the message window after
    downloading, keeping your Mail.app tidy. Useful for batch operations.
    """
    try:
        db = MailDatabase()
        with db.connection() as conn:
            cursor = conn.execute("""
                SELECT m.ROWID, mb.url as mailbox_url, subj.subject as subject_text
                FROM messages m
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.ROWID = ?
            """, (message_id,))
            row = cursor.fetchone()

            if not row:
                return f"Message {message_id} not found"

        # Check if already available
        availability = check_message_availability(message_id, row["mailbox_url"])
        if availability["available_locally"] and not availability.get("is_partial"):
            return f"Message already downloaded. Use `read_email({message_id})` to read it."

        result = trigger_download_silent(message_id, row["mailbox_url"])

        if result["success"]:
            content_info = f" ({result['content_length']} chars)" if result.get("content_length") else ""
            return f"✓ Downloaded{content_info}: {row['subject_text'][:50]}"
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def cleanup_mail_windows() -> str:
    """
    Close all open message windows in Mail.app (keeps main viewer).

    Useful after batch download operations to clean up any leftover windows.
    The main Mail viewer window is preserved.
    """
    try:
        result = close_all_message_windows()

        if result["success"]:
            return result["message"]
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def minimize_mail_app() -> str:
    """
    Minimize all Mail.app windows to the dock.

    Useful after completing email operations to get Mail out of the way.
    """
    try:
        result = minimize_mail()

        if result["success"]:
            return result["message"]
        else:
            return f"Failed: {result['message']}"

    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _find_email_file(message_rowid: int, mailbox_url: str) -> Optional[str]:
    """Find the .emlx file for a message"""
    import subprocess
    import urllib.parse
    from database import MAIL_V10_PATH

    if not mailbox_url:
        return None

    try:
        # Parse mailbox URL
        if "://" not in mailbox_url:
            return None

        _, rest = mailbox_url.split("://", 1)
        parts = rest.split("/", 1)
        account_uuid = parts[0]
        folder_path = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""

        # Build mailbox directory path
        mbox_path = MAIL_V10_PATH / account_uuid

        for part in folder_path.split("/"):
            if part:
                mbox_path = mbox_path / f"{part}.mbox"

        # Find the .emlx file
        if mbox_path.exists():
            result = subprocess.run(
                ['find', str(mbox_path), '-name', f'{message_rowid}*.emlx'],
                capture_output=True,
                text=True,
                timeout=10
            )

            files = [f for f in result.stdout.strip().split('\n') if f]
            if files:
                return files[0]

        return None

    except Exception:
        return None

def _format_messages(messages: list, title: str) -> str:
    """Format a list of messages as markdown"""
    lines = [f"# {title}\n"]
    lines.append("| ID | Status | Date | From | Subject |")
    lines.append("|---:|--------|------|------|---------|")

    for msg in messages:
        # Message ID (needed for read_email, read_thread, etc.)
        msg_id = msg.get("message_id", "?")

        # Status indicators
        read_icon = "📧" if msg.get("is_read", True) else "📬"

        flag_color = msg.get("flag_color")
        if msg.get("is_flagged") and flag_color:
            flag_icon = FLAG_EMOJIS.get(flag_color, "🚩")
        elif msg.get("is_flagged"):
            flag_icon = "🚩"
        else:
            flag_icon = ""

        status = f"{read_icon}{flag_icon}"

        # Date
        date_received = msg.get("date_received")
        if date_received:
            date_str = date_received[:10]  # YYYY-MM-DD
        else:
            date_str = "Unknown"

        # Sender
        sender = str(msg.get("from") or "Unknown")
        if len(sender) > 30:
            sender = sender[:27] + "..."

        # Subject
        subject = str(msg.get("subject") or "(No subject)")
        if len(subject) > 40:
            subject = subject[:37] + "..."

        lines.append(f"| {msg_id} | {status} | {date_str} | {sender} | {subject} |")

    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run the MCP server"""
    import argparse

    parser = argparse.ArgumentParser(description="Apple Mail MCP Server")
    parser.add_argument("--test", action="store_true", help="Run test commands")
    args = parser.parse_args()

    if args.test:
        # Test mode - run some commands directly
        print("Testing Apple Mail MCP Server\n")
        print("=" * 60)

        print("\n1. List Flag Colors:")
        print(list_flag_colors())

        print("\n2. Find Inbox mailbox:")
        print(find_mailbox("Inbox"))

        print("\n3. Recent messages (5):")
        print(get_recent_messages(limit=5))

        print("\n4. Unread messages (5):")
        print(get_unread_messages(limit=5))

        print("\nTests complete!")
        return 0

    # Run the MCP server
    mcp.run()


if __name__ == "__main__":
    sys.exit(main() or 0)
