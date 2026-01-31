#!/usr/bin/env python3
"""
Apple Mail Message Access

Query and retrieve messages from the Mail database.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
import urllib.parse

from database import MailDatabase, MAIL_V10_PATH


class MessageQuery:
    """Query messages from Apple Mail database"""

    def __init__(self, db: Optional[MailDatabase] = None):
        """
        Initialize message query handler.

        Args:
            db: Optional MailDatabase instance (creates new one if not provided)
        """
        self.db = db or MailDatabase()

    def get_recent_messages(
        self,
        mailbox_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
        include_read: bool = True,
        exclude_folders: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages, optionally filtered by mailbox.

        Args:
            mailbox_id: Filter by specific mailbox ROWID
            limit: Maximum number of messages to return
            offset: Number of messages to skip (for pagination)
            include_read: Whether to include read messages (False = unread only)
            exclude_folders: List of folder name patterns to exclude (e.g., ["Junk", "Spam"])

        Returns:
            List of message dictionaries
        """
        with self.db.connection() as conn:
            # Build subquery to get message ROWIDs first (fast, no joins)
            subquery = "SELECT ROWID FROM messages WHERE 1=1"
            subquery_params = []

            if mailbox_id is not None:
                subquery += " AND mailbox = ?"
                subquery_params.append(mailbox_id)

            if not include_read:
                subquery += " AND read = 0"

            # Handle exclude_folders by getting excluded mailbox IDs
            excluded_mailbox_ids = []
            if exclude_folders:
                like_clauses = " OR ".join(["url LIKE ?" for _ in exclude_folders])
                exclude_query = f"SELECT ROWID FROM mailboxes WHERE {like_clauses}"
                exclude_params = [f"%{pattern}%" for pattern in exclude_folders]
                excluded_mailbox_ids = [row[0] for row in conn.execute(exclude_query, exclude_params).fetchall()]
                if excluded_mailbox_ids:
                    placeholders = ",".join("?" * len(excluded_mailbox_ids))
                    subquery += f" AND mailbox NOT IN ({placeholders})"
                    subquery_params.extend(excluded_mailbox_ids)

            subquery += " ORDER BY date_received DESC LIMIT ? OFFSET ?"
            subquery_params.extend([limit, offset])

            # Main query joins only the rows we need
            query = f"""
                SELECT
                    m.ROWID as message_id,
                    m.mailbox,
                    m.subject_prefix,
                    subj.subject as subject_text,
                    m.date_sent,
                    m.date_received,
                    m.read,
                    m.flagged,
                    m.flag_color,
                    m.size,
                    m.conversation_id,
                    mgd.message_id_header,
                    mb.url as mailbox_url,
                    sender_addr.address as sender_email,
                    sender_addr.comment as sender_name
                FROM messages m
                LEFT JOIN message_global_data mgd ON m.global_message_id = mgd.ROWID
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
                WHERE m.ROWID IN ({subquery})
                ORDER BY m.date_received DESC
            """
            params = subquery_params

            cursor = conn.execute(query, params)

            messages = []
            for row in cursor.fetchall():
                # Build subject with prefix
                subject = row["subject_text"] or ""
                if row["subject_prefix"]:
                    subject = row["subject_prefix"] + subject

                # Build sender display
                sender_email = row["sender_email"]
                sender_name = row["sender_name"]
                if sender_name and sender_email:
                    sender = f"{sender_name} <{sender_email}>"
                elif sender_email:
                    sender = sender_email
                else:
                    sender = None

                msg = {
                    "message_id": row["message_id"],
                    "mailbox_id": row["mailbox"],
                    "subject": subject,
                    "date_sent": self._format_timestamp(row["date_sent"]),
                    "date_received": self._format_timestamp(row["date_received"]),
                    "is_read": bool(row["read"]),
                    "is_flagged": bool(row["flagged"]),
                    "flag_color": row["flag_color"],
                    "size_bytes": row["size"],
                    "conversation_id": row["conversation_id"],
                    "rfc_message_id": row["message_id_header"],
                    "mailbox_url": row["mailbox_url"],
                    "from": sender,
                    # Note: file_path is computed on-demand when reading emails
                    # to avoid expensive file system traversal during listings
                }

                messages.append(msg)

            return messages

    def get_messages_by_folder(
        self,
        folder_name: str,
        limit: int = 20,
        include_read: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get messages from a folder by name.

        Args:
            folder_name: Folder name to search for (e.g., "Projects")
            limit: Maximum number of messages
            include_read: Whether to include read messages

        Returns:
            List of message dictionaries
        """
        # Find matching mailboxes
        mailboxes = self.db.find_mailbox(folder_name)

        if not mailboxes:
            return []

        # Get messages from all matching mailboxes
        all_messages = []
        for mb in mailboxes:
            messages = self.get_recent_messages(
                mailbox_id=mb["id"],
                limit=limit,
                include_read=include_read
            )
            all_messages.extend(messages)

        # Sort by date received and limit
        all_messages.sort(key=lambda m: m["date_received"] or "", reverse=True)
        return all_messages[:limit]

    def _get_message_addresses(self, conn, message_id: int) -> Dict[str, Any]:
        """Get sender and recipient addresses for a message"""
        result = {
            "from": None,
            "to": [],
            "cc": [],
        }

        try:
            # Query addresses table
            # Address types: 0=From, 1=To, 2=Cc, 3=Bcc, etc.
            cursor = conn.execute("""
                SELECT
                    a.address,
                    a.comment,
                    a.type
                FROM addresses a
                WHERE a.message = ?
                ORDER BY a.type, a.position
            """, (message_id,))

            for row in cursor.fetchall():
                addr = row["address"]
                comment = row["comment"]  # Display name
                addr_type = row["type"]

                # Format as "Name <email>" if comment exists
                if comment:
                    formatted = f"{comment} <{addr}>"
                else:
                    formatted = addr

                if addr_type == 0:  # From
                    result["from"] = formatted
                elif addr_type == 1:  # To
                    result["to"].append(formatted)
                elif addr_type == 2:  # Cc
                    result["cc"].append(formatted)

        except Exception:
            pass  # Return partial results if address lookup fails

        return result

    def _format_timestamp(self, timestamp: Optional[float]) -> Optional[str]:
        """Convert Mail database timestamp to ISO format"""
        if timestamp is None:
            return None

        try:
            # Mail database uses Unix timestamps (seconds since 1970-01-01)
            dt = datetime.fromtimestamp(timestamp)
            return dt.isoformat()
        except (ValueError, OSError):
            return None

    def _build_file_path(self, message_rowid: int, mailbox_url: str) -> Optional[str]:
        """
        Find the file path for an email message.

        Mail.app stores emails in a nested structure:
        ~/Library/Mail/V10/{account}/{folder}.mbox/{UUID}/Data/{X}/Messages/{rowid}.emlx
        or sometimes {rowid}.partial.emlx for partially downloaded emails.

        Uses pathlib.rglob for fast file lookup (16-125x faster than subprocess find).
        """
        if not mailbox_url:
            return None

        try:
            # Parse mailbox URL: imap://ACCOUNT-UUID/FOLDER/PATH or ews://...
            if "://" not in mailbox_url:
                return None

            _, rest = mailbox_url.split("://", 1)
            parts = rest.split("/", 1)
            account_uuid = parts[0]
            folder_path = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""

            # Build mbox path
            mbox_path = MAIL_V10_PATH / account_uuid

            for part in folder_path.split("/"):
                if part:
                    mbox_path = mbox_path / f"{part}.mbox"

            # Use pathlib.rglob for fast recursive search
            if mbox_path.exists():
                partial_path = None
                for path in mbox_path.rglob(f'{message_rowid}*.emlx'):
                    if '.partial.' not in path.name:
                        return str(path)
                    partial_path = str(path)
                # Fall back to partial file if no full version found
                if partial_path:
                    return partial_path

            return None

        except Exception:
            return None

    def search_messages(
        self,
        subject_contains: Optional[str] = None,
        sender_contains: Optional[str] = None,
        days_back: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search messages with various filters.

        Args:
            subject_contains: Filter by subject (case-insensitive)
            sender_contains: Filter by sender address (case-insensitive)
            days_back: Only include messages from the last N days
            limit: Maximum results

        Returns:
            List of matching messages
        """
        with self.db.connection() as conn:
            # messages.sender is a direct FK to addresses.ROWID
            query = """
                SELECT DISTINCT
                    m.ROWID as message_id,
                    m.mailbox,
                    m.subject_prefix,
                    subj.subject as subject_text,
                    m.date_sent,
                    m.date_received,
                    m.read,
                    m.flagged,
                    m.flag_color,
                    mgd.message_id_header,
                    mb.url as mailbox_url,
                    sender_addr.address as sender_email,
                    sender_addr.comment as sender_name
                FROM messages m
                LEFT JOIN message_global_data mgd ON m.global_message_id = mgd.ROWID
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
            """
            conditions = []
            params = []

            if sender_contains:
                conditions.append("(sender_addr.address LIKE ? OR sender_addr.comment LIKE ?)")
                params.extend([f"%{sender_contains}%", f"%{sender_contains}%"])

            if subject_contains:
                conditions.append("subj.subject LIKE ?")
                params.append(f"%{subject_contains}%")

            if days_back:
                # Calculate timestamp for N days ago (Unix timestamp)
                from datetime import timedelta
                cutoff = datetime.now() - timedelta(days=days_back)
                cutoff_timestamp = cutoff.timestamp()
                conditions.append("m.date_received >= ?")
                params.append(cutoff_timestamp)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY m.date_received DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)

            messages = []
            for row in cursor.fetchall():
                # Build subject with prefix
                subject = row["subject_text"] or ""
                if row["subject_prefix"]:
                    subject = row["subject_prefix"] + subject

                # Build sender display
                sender_email = row["sender_email"]
                sender_name = row["sender_name"]
                if sender_name and sender_email:
                    sender = f"{sender_name} <{sender_email}>"
                elif sender_email:
                    sender = sender_email
                else:
                    sender = None

                msg = {
                    "message_id": row["message_id"],
                    "subject": subject,
                    "date_received": self._format_timestamp(row["date_received"]),
                    "is_read": bool(row["read"]),
                    "is_flagged": bool(row["flagged"]),
                    "flag_color": row["flag_color"],
                    "rfc_message_id": row["message_id_header"],
                    "from": sender,
                }
                messages.append(msg)

            return messages


def main():
    """CLI tool to query messages"""
    import argparse

    parser = argparse.ArgumentParser(description="Query Apple Mail messages")
    parser.add_argument("command", choices=["recent", "folder", "search", "unread"],
                        help="Command to run")
    parser.add_argument("--folder", help="Folder name for folder command")
    parser.add_argument("--subject", help="Subject search term")
    parser.add_argument("--sender", help="Sender search term")
    parser.add_argument("--days", type=int, help="Limit to last N days")
    parser.add_argument("--limit", type=int, default=10, help="Max results")

    args = parser.parse_args()

    try:
        query = MessageQuery()

        if args.command == "recent":
            messages = query.get_recent_messages(limit=args.limit)
            print(f"Recent messages ({len(messages)}):\n")
            for msg in messages:
                read_marker = "  " if msg["is_read"] else "* "
                flag_marker = "!" if msg["is_flagged"] else " "
                print(f"{read_marker}{flag_marker} {msg['date_received'][:10] if msg['date_received'] else 'No date'}")
                print(f"    From: {msg['from'] or 'Unknown'}")
                print(f"    Subject: {msg['subject'] or '(No subject)'}")
                print()

        elif args.command == "folder":
            if not args.folder:
                print("Error: --folder required")
                return 1
            messages = query.get_messages_by_folder(args.folder, limit=args.limit)
            print(f"Messages in '{args.folder}' ({len(messages)}):\n")
            for msg in messages:
                print(f"  {msg['date_received'][:10] if msg['date_received'] else 'No date'}")
                print(f"    From: {msg['from'] or 'Unknown'}")
                print(f"    Subject: {msg['subject'] or '(No subject)'}")
                print()

        elif args.command == "unread":
            messages = query.get_recent_messages(limit=args.limit, include_read=False)
            print(f"Unread messages ({len(messages)}):\n")
            for msg in messages:
                print(f"  {msg['date_received'][:10] if msg['date_received'] else 'No date'}")
                print(f"    From: {msg['from'] or 'Unknown'}")
                print(f"    Subject: {msg['subject'] or '(No subject)'}")
                print()

        elif args.command == "search":
            messages = query.search_messages(
                subject_contains=args.subject,
                sender_contains=args.sender,
                days_back=args.days,
                limit=args.limit
            )
            print(f"Search results ({len(messages)}):\n")
            for msg in messages:
                print(f"  {msg['date_received'][:10] if msg['date_received'] else 'No date'}")
                print(f"    From: {msg['from'] or 'Unknown'}")
                print(f"    Subject: {msg['subject'] or '(No subject)'}")
                print()

        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
