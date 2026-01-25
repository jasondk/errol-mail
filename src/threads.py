#!/usr/bin/env python3
"""
Apple Mail Thread/Conversation Handling

Retrieve email threads (conversations) from the Mail database.
"""

import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from database import MailDatabase, MAIL_V10_PATH
from email_reader import parse_emlx_file


class ThreadQuery:
    """Query email threads/conversations from Apple Mail"""

    def __init__(self, db: Optional[MailDatabase] = None):
        """
        Initialize thread query handler.

        Args:
            db: Optional MailDatabase instance
        """
        self.db = db or MailDatabase()

    def get_thread_by_message_id(
        self,
        message_id: int,
        include_body: bool = True,
        strip_quotes: bool = True,
        max_body_length: int = 5000
    ) -> Dict[str, Any]:
        """
        Get all messages in a thread by any message's database ID.

        Args:
            message_id: Database ROWID of any message in the thread
            include_body: Whether to include message body text
            strip_quotes: Strip quoted content (useful for threads)
            max_body_length: Maximum body length per message

        Returns:
            Thread information with all messages
        """
        with self.db.connection() as conn:
            # First get the conversation_id for this message
            cursor = conn.execute(
                "SELECT conversation_id FROM messages WHERE ROWID = ?",
                (message_id,)
            )
            row = cursor.fetchone()

            if not row or not row["conversation_id"]:
                return {
                    "success": False,
                    "error": f"Message {message_id} not found or has no conversation"
                }

            conversation_id = row["conversation_id"]

            return self.get_thread_by_conversation_id(
                conversation_id,
                include_body=include_body,
                strip_quotes=strip_quotes,
                max_body_length=max_body_length
            )

    def get_thread_by_conversation_id(
        self,
        conversation_id: int,
        include_body: bool = True,
        strip_quotes: bool = True,
        max_body_length: int = 5000
    ) -> Dict[str, Any]:
        """
        Get all messages in a thread by conversation ID.

        Args:
            conversation_id: The conversation_id from the messages table
            include_body: Whether to include message body text
            strip_quotes: Strip quoted content
            max_body_length: Maximum body length per message

        Returns:
            Thread information with all messages
        """
        with self.db.connection() as conn:
            # Get all messages in this conversation
            cursor = conn.execute("""
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
                    mgd.message_id_header,
                    mb.url as mailbox_url,
                    sender_addr.address as sender_email,
                    sender_addr.comment as sender_name
                FROM messages m
                LEFT JOIN message_global_data mgd ON m.global_message_id = mgd.ROWID
                LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
                WHERE m.conversation_id = ?
                ORDER BY m.date_sent ASC
            """, (conversation_id,))

            rows = cursor.fetchall()

            if not rows:
                return {
                    "success": False,
                    "error": f"No messages found for conversation {conversation_id}"
                }

            # Build thread info
            messages = []
            thread_subject = None

            for row in rows:
                subject = (row["subject_prefix"] or "") + (row["subject_text"] or "")
                if not thread_subject:
                    # Use first message's subject as thread subject
                    thread_subject = subject.lstrip("Re: ").lstrip("RE: ").lstrip("Fwd: ").lstrip("FW: ")

                sender_email = row["sender_email"]
                sender_name = row["sender_name"]
                if sender_name and sender_email:
                    sender = f"{sender_name} <{sender_email}>"
                elif sender_email:
                    sender = sender_email
                else:
                    sender = "Unknown"

                # Format date
                date_sent = row["date_sent"]
                if date_sent:
                    try:
                        date_str = datetime.fromtimestamp(date_sent).isoformat()
                    except (ValueError, OSError):
                        date_str = None
                else:
                    date_str = None

                msg_info = {
                    "message_id": row["message_id"],
                    "rfc_message_id": row["message_id_header"],
                    "subject": subject,
                    "from": sender,
                    "date": date_str,
                    "is_read": bool(row["read"]),
                    "is_flagged": bool(row["flagged"]),
                    "flag_color": row["flag_color"],
                }

                # Get file path and optionally read body
                if include_body:
                    file_path = self._find_email_file(
                        row["message_id"],
                        row["mailbox_url"]
                    )

                    if file_path:
                        msg_info["file_path"] = file_path

                        # Parse the email file
                        email_data = parse_emlx_file(
                            file_path,
                            max_body_length=max_body_length,
                            strip_quotes=strip_quotes
                        )

                        if email_data["success"]:
                            msg_info["body"] = email_data["body_text"]
                            msg_info["attachments"] = email_data.get("attachments", [])
                            if email_data.get("truncated"):
                                msg_info["body_truncated"] = True
                        else:
                            msg_info["body_error"] = email_data.get("error", "Failed to read")

                messages.append(msg_info)

            return {
                "success": True,
                "conversation_id": conversation_id,
                "subject": thread_subject,
                "message_count": len(messages),
                "messages": messages
            }

    def _find_email_file(self, message_rowid: int, mailbox_url: str) -> Optional[str]:
        """Find the .emlx file for a message"""
        if not mailbox_url:
            return None

        try:
            # Parse mailbox URL
            if "://" not in mailbox_url:
                return None

            protocol, rest = mailbox_url.split("://", 1)
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
                # Use find command for speed
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

    def get_conversation_id_for_message(self, message_id: int) -> Optional[int]:
        """Get the conversation_id for a message by its database ROWID"""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT conversation_id FROM messages WHERE ROWID = ?",
                (message_id,)
            )
            row = cursor.fetchone()
            return row["conversation_id"] if row else None

    def get_thread_summary(self, conversation_id: int) -> Dict[str, Any]:
        """
        Get a summary of a thread without full message bodies.

        Args:
            conversation_id: The conversation_id

        Returns:
            Thread summary with participants, date range, etc.
        """
        with self.db.connection() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as message_count,
                    MIN(m.date_sent) as first_date,
                    MAX(m.date_sent) as last_date,
                    SUM(CASE WHEN m.read = 0 THEN 1 ELSE 0 END) as unread_count,
                    SUM(CASE WHEN m.flagged = 1 THEN 1 ELSE 0 END) as flagged_count
                FROM messages m
                WHERE m.conversation_id = ?
            """, (conversation_id,))

            row = cursor.fetchone()

            if not row or row["message_count"] == 0:
                return {
                    "success": False,
                    "error": f"No messages found for conversation {conversation_id}"
                }

            # Get participants
            cursor = conn.execute("""
                SELECT DISTINCT
                    sender_addr.address as email,
                    sender_addr.comment as name
                FROM messages m
                LEFT JOIN addresses sender_addr ON m.sender = sender_addr.ROWID
                WHERE m.conversation_id = ?
            """, (conversation_id,))

            participants = []
            for p_row in cursor.fetchall():
                if p_row["name"] and p_row["email"]:
                    participants.append(f"{p_row['name']} <{p_row['email']}>")
                elif p_row["email"]:
                    participants.append(p_row["email"])

            # Get subject from first message
            cursor = conn.execute("""
                SELECT m.subject_prefix, subj.subject as subject_text
                FROM messages m
                LEFT JOIN subjects subj ON m.subject = subj.ROWID
                WHERE m.conversation_id = ?
                ORDER BY m.date_sent ASC
                LIMIT 1
            """, (conversation_id,))

            subj_row = cursor.fetchone()
            subject = ""
            if subj_row:
                subject = (subj_row["subject_prefix"] or "") + (subj_row["subject_text"] or "")
                subject = subject.lstrip("Re: ").lstrip("RE: ").lstrip("Fwd: ").lstrip("FW: ")

            # Format dates
            first_date = None
            last_date = None
            if row["first_date"]:
                try:
                    first_date = datetime.fromtimestamp(row["first_date"]).isoformat()
                except (ValueError, OSError):
                    pass
            if row["last_date"]:
                try:
                    last_date = datetime.fromtimestamp(row["last_date"]).isoformat()
                except (ValueError, OSError):
                    pass

            return {
                "success": True,
                "conversation_id": conversation_id,
                "subject": subject,
                "message_count": row["message_count"],
                "unread_count": row["unread_count"],
                "flagged_count": row["flagged_count"],
                "participants": participants,
                "first_message_date": first_date,
                "last_message_date": last_date
            }


def main():
    """CLI test tool"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python threads.py <message_id>")
        print("\nExample:")
        print("  python threads.py 12345")
        sys.exit(1)

    message_id = int(sys.argv[1])
    print(f"Getting thread for message {message_id}\n")

    query = ThreadQuery()
    result = query.get_thread_by_message_id(message_id, include_body=True)

    if result["success"]:
        print(f"Thread: {result['subject']}")
        print(f"Messages: {result['message_count']}")
        print("=" * 60)

        for i, msg in enumerate(result["messages"], 1):
            print(f"\n[{i}] From: {msg['from']}")
            print(f"    Date: {msg['date']}")
            print(f"    Subject: {msg['subject']}")
            if msg.get("body"):
                print(f"    Body:\n{msg['body'][:500]}...")
            if msg.get("attachments"):
                print(f"    Attachments: {[a['filename'] for a in msg['attachments']]}")
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
