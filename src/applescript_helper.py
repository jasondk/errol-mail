#!/usr/bin/env python3
"""
AppleScript helpers for Apple Mail interaction.

Provides functionality that requires direct Mail.app interaction,
such as triggering downloads of server-only messages.
"""

import subprocess
import logging
import time
from typing import Dict, Any, Optional

# Get logger from parent module
_logger = logging.getLogger("errol-mail.applescript")


def run_applescript(script: str, timeout: int = 30, operation: str = "unknown") -> tuple[bool, str]:
    """
    Execute an AppleScript and return the result.

    Args:
        script: AppleScript code to execute
        timeout: Maximum execution time in seconds
        operation: Description of the operation for logging

    Returns:
        Tuple of (success, output_or_error)
    """
    start_time = time.perf_counter()
    _logger.debug(f"AppleScript start: {operation}")

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.perf_counter() - start_time

        if result.returncode == 0:
            if elapsed > 2.0:
                _logger.warning(f"AppleScript SLOW ({operation}): {elapsed:.2f}s")
            else:
                _logger.debug(f"AppleScript done ({operation}): {elapsed:.3f}s")
            return True, result.stdout.strip()
        else:
            _logger.error(f"AppleScript failed ({operation}) after {elapsed:.3f}s: {result.stderr.strip()}")
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start_time
        _logger.error(f"AppleScript TIMEOUT ({operation}) after {elapsed:.2f}s")
        return False, "AppleScript execution timed out"
    except Exception as e:
        elapsed = time.perf_counter() - start_time
        _logger.error(f"AppleScript error ({operation}) after {elapsed:.3f}s: {e}")
        return False, str(e)


def get_mailbox_info_from_url(mailbox_url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse mailbox URL to get account identifier and mailbox path.

    Args:
        mailbox_url: URL like "imap://UUID/[Gmail]/All Mail"

    Returns:
        Tuple of (account_uuid, mailbox_path)
    """
    if "://" not in mailbox_url:
        return None, None

    import urllib.parse
    _, rest = mailbox_url.split("://", 1)
    parts = rest.split("/", 1)
    account_uuid = parts[0]
    mailbox_path = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""

    return account_uuid, mailbox_path


def find_account_by_uuid(account_uuid: str) -> tuple[bool, str]:
    """
    Find Mail.app account name by its UUID.

    Args:
        account_uuid: The account UUID from mailbox URL

    Returns:
        Tuple of (found, account_name_or_error)
    """
    script = f'''
tell application "Mail"
    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            return name of acct
        end if
    end repeat
    return "NOT_FOUND"
end tell
'''
    success, result = run_applescript(script, operation="find_account_by_uuid")
    if success and result != "NOT_FOUND":
        return True, result
    return False, f"Account with UUID {account_uuid} not found"


def trigger_message_download(message_id: int, mailbox_url: str, timeout: int = 60) -> Dict[str, Any]:
    """
    Trigger Mail.app to download a message from the server.

    This works by using AppleScript to open the message, which causes
    Mail to fetch it from the server if not already downloaded.

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database
        timeout: Maximum time to wait for download

    Returns:
        Dict with keys: success, message, content_length (if successful)
    """
    account_uuid, mailbox_path = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid or not mailbox_path:
        return {
            "success": False,
            "message": f"Could not parse mailbox URL: {mailbox_url}"
        }

    # Build AppleScript to find and open the message
    # We need to map the mailbox path to AppleScript mailbox reference
    # [Gmail]/All Mail -> mailbox "[Gmail]/All Mail" of account "..."

    script = f'''
tell application "Mail"
    set targetId to {message_id}
    set targetMailboxPath to "{mailbox_path}"

    -- Find the account
    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            -- Found account, now find mailbox
            try
                set targetMailbox to mailbox targetMailboxPath of acct

                -- Find message by ID (this can be slow for large mailboxes)
                set matchingMsgs to (every message of targetMailbox whose id is targetId)

                if (count of matchingMsgs) > 0 then
                    set msg to item 1 of matchingMsgs

                    -- Open message to trigger download
                    open msg
                    delay 1

                    -- Try to get content (this forces download)
                    try
                        set msgContent to content of msg
                        return "SUCCESS:" & (length of msgContent)
                    on error
                        return "OPENED:Message opened but content not yet available"
                    end try
                else
                    return "ERROR:Message ID " & targetId & " not found in mailbox"
                end if
            on error errMsg
                return "ERROR:Could not access mailbox - " & errMsg
            end try
        end if
    end repeat

    return "ERROR:Account not found"
end tell
'''

    success, result = run_applescript(script, timeout=timeout, operation=f"download_message_{message_id}")

    if not success:
        return {
            "success": False,
            "message": f"AppleScript error: {result}"
        }

    if result.startswith("SUCCESS:"):
        content_length = int(result.split(":")[1])
        return {
            "success": True,
            "message": "Message downloaded successfully",
            "content_length": content_length
        }
    elif result.startswith("OPENED:"):
        return {
            "success": True,
            "message": result.split(":", 1)[1],
            "content_length": None
        }
    else:
        error_msg = result.split(":", 1)[1] if ":" in result else result
        return {
            "success": False,
            "message": error_msg
        }


def check_message_availability(message_id: int, mailbox_url: str) -> Dict[str, Any]:
    """
    Check if a message is available locally or only on server.

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database

    Returns:
        Dict with: available_locally, can_download, message
    """
    import subprocess
    from pathlib import Path

    # First check if local file exists
    account_uuid, _ = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid:
        return {
            "available_locally": False,
            "can_download": False,
            "message": "Invalid mailbox URL"
        }

    # Build path and search for .emlx file
    mail_path = Path.home() / "Library" / "Mail" / "V10" / account_uuid

    try:
        # Search for the message file
        result = subprocess.run(
            ['find', str(mail_path), '-name', f'{message_id}*.emlx'],
            capture_output=True,
            text=True,
            timeout=15
        )

        files = [f for f in result.stdout.strip().split('\n') if f]

        if files:
            # Check if it's a full or partial file
            is_partial = any('.partial.' in f for f in files)
            return {
                "available_locally": True,
                "is_partial": is_partial,
                "can_download": True,
                "file_path": files[0],
                "message": "Message available locally" + (" (partial)" if is_partial else "")
            }
        else:
            return {
                "available_locally": False,
                "can_download": True,
                "message": "Message is on server only. Use trigger_message_download() to fetch it."
            }

    except subprocess.TimeoutExpired:
        return {
            "available_locally": False,
            "can_download": True,
            "message": "Search timed out, message may be on server"
        }
    except Exception as e:
        return {
            "available_locally": False,
            "can_download": False,
            "message": f"Error checking availability: {e}"
        }


def open_message_in_mail(message_id: int, mailbox_url: str) -> Dict[str, Any]:
    """
    Open a message in Mail.app's viewer window.

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database

    Returns:
        Dict with success status and message
    """
    account_uuid, mailbox_path = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid or not mailbox_path:
        return {
            "success": False,
            "message": f"Could not parse mailbox URL: {mailbox_url}"
        }

    script = f'''
tell application "Mail"
    activate
    set targetId to {message_id}

    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            try
                set targetMailbox to mailbox "{mailbox_path}" of acct
                set matchingMsgs to (every message of targetMailbox whose id is targetId)

                if (count of matchingMsgs) > 0 then
                    set msg to item 1 of matchingMsgs
                    open msg
                    return "SUCCESS"
                end if
            end try
        end if
    end repeat

    return "NOT_FOUND"
end tell
'''

    success, result = run_applescript(script, timeout=30, operation=f"open_message_{message_id}")

    if success and result == "SUCCESS":
        return {
            "success": True,
            "message": "Message opened in Mail.app"
        }
    else:
        return {
            "success": False,
            "message": f"Could not open message: {result}"
        }


# ============================================================================
# HEADLESS MESSAGE MODIFICATION TOOLS
# ============================================================================

# Flag color mapping
FLAG_COLORS = {
    "red": 1, "orange": 2, "yellow": 3, "green": 4,
    "blue": 5, "purple": 6, "gray": 7, "grey": 7,
    "none": -1, "clear": -1
}


def set_message_flag(message_id: int, mailbox_url: str, color: str) -> Dict[str, Any]:
    """
    Set the flag color on a message (runs headless, no windows).

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database
        color: Flag color name (red, orange, yellow, green, blue, purple, gray)

    Returns:
        Dict with success status and message
    """
    color_lower = color.lower()
    if color_lower not in FLAG_COLORS:
        return {
            "success": False,
            "message": f"Invalid color: {color}. Valid colors: red, orange, yellow, green, blue, purple, gray"
        }

    flag_index = FLAG_COLORS[color_lower]
    account_uuid, mailbox_path = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid or not mailbox_path:
        return {
            "success": False,
            "message": f"Could not parse mailbox URL: {mailbox_url}"
        }

    script = f'''
tell application "Mail"
    set targetId to {message_id}

    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            try
                set targetMailbox to mailbox "{mailbox_path}" of acct
                set matchingMsgs to (every message of targetMailbox whose id is targetId)

                if (count of matchingMsgs) > 0 then
                    set msg to item 1 of matchingMsgs
                    set flag index of msg to {flag_index}
                    return "SUCCESS:" & (flag index of msg)
                else
                    return "ERROR:Message not found"
                end if
            on error errMsg
                return "ERROR:" & errMsg
            end try
        end if
    end repeat

    return "ERROR:Account not found"
end tell
'''

    success, result = run_applescript(script, timeout=30, operation=f"set_flag_{message_id}_{color}")

    if success and result.startswith("SUCCESS:"):
        new_flag = int(result.split(":")[1])
        return {
            "success": True,
            "message": f"Flag set to {color}",
            "flag_index": new_flag
        }
    else:
        error_msg = result.split(":", 1)[1] if ":" in result else result
        return {
            "success": False,
            "message": f"Failed to set flag: {error_msg}"
        }


def clear_message_flag(message_id: int, mailbox_url: str) -> Dict[str, Any]:
    """
    Remove the flag from a message (runs headless, no windows).

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database

    Returns:
        Dict with success status and message
    """
    return set_message_flag(message_id, mailbox_url, "none")


def set_message_read_status(message_id: int, mailbox_url: str, is_read: bool) -> Dict[str, Any]:
    """
    Set the read/unread status of a message (runs headless, no windows).

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database
        is_read: True to mark as read, False to mark as unread

    Returns:
        Dict with success status and message
    """
    account_uuid, mailbox_path = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid or not mailbox_path:
        return {
            "success": False,
            "message": f"Could not parse mailbox URL: {mailbox_url}"
        }

    read_value = "true" if is_read else "false"

    script = f'''
tell application "Mail"
    set targetId to {message_id}

    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            try
                set targetMailbox to mailbox "{mailbox_path}" of acct
                set matchingMsgs to (every message of targetMailbox whose id is targetId)

                if (count of matchingMsgs) > 0 then
                    set msg to item 1 of matchingMsgs
                    set read status of msg to {read_value}
                    return "SUCCESS:" & (read status of msg)
                else
                    return "ERROR:Message not found"
                end if
            on error errMsg
                return "ERROR:" & errMsg
            end try
        end if
    end repeat

    return "ERROR:Account not found"
end tell
'''

    read_op = "mark_read" if is_read else "mark_unread"
    success, result = run_applescript(script, timeout=30, operation=f"{read_op}_{message_id}")

    if success and result.startswith("SUCCESS:"):
        new_status = result.split(":")[1] == "true"
        status_str = "read" if new_status else "unread"
        return {
            "success": True,
            "message": f"Message marked as {status_str}",
            "is_read": new_status
        }
    else:
        error_msg = result.split(":", 1)[1] if ":" in result else result
        return {
            "success": False,
            "message": f"Failed to set read status: {error_msg}"
        }


def trigger_message_download_silent(message_id: int, mailbox_url: str, timeout: int = 60) -> Dict[str, Any]:
    """
    Trigger Mail.app to download a message, then close the window.

    This opens the message to trigger download, waits for content,
    then closes the message window to keep things tidy.

    Args:
        message_id: Database ROWID of the message
        mailbox_url: Mailbox URL from database
        timeout: Maximum time to wait for download

    Returns:
        Dict with keys: success, message, content_length (if successful)
    """
    account_uuid, mailbox_path = get_mailbox_info_from_url(mailbox_url)

    if not account_uuid or not mailbox_path:
        return {
            "success": False,
            "message": f"Could not parse mailbox URL: {mailbox_url}"
        }

    script = f'''
tell application "Mail"
    set targetId to {message_id}
    set targetMailboxPath to "{mailbox_path}"

    -- Count windows before opening
    set windowCountBefore to count of windows

    -- Find the account
    repeat with acct in every account
        set acctId to id of acct
        if acctId contains "{account_uuid}" then
            try
                set targetMailbox to mailbox targetMailboxPath of acct

                -- Find message by ID
                set matchingMsgs to (every message of targetMailbox whose id is targetId)

                if (count of matchingMsgs) > 0 then
                    set msg to item 1 of matchingMsgs
                    set msgSubject to subject of msg

                    -- Open message to trigger download
                    open msg
                    delay 1

                    -- Try to get content (this forces download)
                    set contentLength to 0
                    try
                        set msgContent to content of msg
                        set contentLength to length of msgContent
                    end try

                    -- Close the window we opened
                    delay 0.3
                    try
                        -- Find and close the window by subject match
                        set targetWindow to first window whose name contains msgSubject
                        close targetWindow
                        delay 0.2
                    end try

                    if contentLength > 0 then
                        return "SUCCESS:" & contentLength
                    else
                        return "OPENED:Message opened but content not yet available"
                    end if
                else
                    return "ERROR:Message ID " & targetId & " not found in mailbox"
                end if
            on error errMsg
                return "ERROR:Could not access mailbox - " & errMsg
            end try
        end if
    end repeat

    return "ERROR:Account not found"
end tell
'''

    success, result = run_applescript(script, timeout=timeout, operation=f"download_silent_{message_id}")

    if not success:
        return {
            "success": False,
            "message": f"AppleScript error: {result}"
        }

    if result.startswith("SUCCESS:"):
        content_length = int(result.split(":")[1])
        return {
            "success": True,
            "message": "Message downloaded successfully (window closed)",
            "content_length": content_length
        }
    elif result.startswith("OPENED:"):
        return {
            "success": True,
            "message": result.split(":", 1)[1] + " (window closed)",
            "content_length": None
        }
    else:
        error_msg = result.split(":", 1)[1] if ":" in result else result
        return {
            "success": False,
            "message": error_msg
        }


def minimize_mail() -> Dict[str, Any]:
    """
    Minimize all Mail.app windows (useful after batch operations).

    Returns:
        Dict with success status
    """
    script = '''
tell application "Mail"
    set miniaturized of every window to true
end tell
return "SUCCESS"
'''

    success, result = run_applescript(script, timeout=10, operation="minimize_mail")

    if success:
        return {"success": True, "message": "Mail windows minimized"}
    else:
        return {"success": False, "message": f"Failed to minimize: {result}"}


def close_all_message_windows() -> Dict[str, Any]:
    """
    Close all open message windows in Mail (keeps main viewer open).

    Returns:
        Dict with success status and count of closed windows
    """
    script = '''
tell application "Mail"
    activate
    delay 0.3

    set closedCount to 0

    -- Message windows have " — " in their name (Subject — Mailbox)
    -- Main viewer windows are just: "Flagged", "All Inboxes", etc.
    -- Compose windows start with "New Message"

    -- Close message windows one at a time
    repeat 20 times
        try
            set targetWindow to first window whose name contains " — "
            close targetWindow
            set closedCount to closedCount + 1
            delay 0.3
        on error
            -- No more message windows
            exit repeat
        end try
    end repeat

    return "SUCCESS:" & closedCount
end tell
'''

    success, result = run_applescript(script, timeout=30, operation="close_all_message_windows")

    if success and result.startswith("SUCCESS:"):
        count = int(result.split(":")[1])
        return {
            "success": True,
            "message": f"Closed {count} message window(s)",
            "closed_count": count
        }
    else:
        return {"success": False, "message": f"Failed: {result}"}


if __name__ == "__main__":
    # Test the helper
    import sys

    if len(sys.argv) < 2:
        print("Usage: python applescript_helper.py <command> [args]")
        print("Commands:")
        print("  check <message_id> <mailbox_url>  - Check message availability")
        print("  download <message_id> <mailbox_url> - Download message (silent)")
        print("  flag <message_id> <mailbox_url> <color> - Set flag")
        print("  unflag <message_id> <mailbox_url> - Clear flag")
        print("  read <message_id> <mailbox_url>   - Mark as read")
        print("  unread <message_id> <mailbox_url> - Mark as unread")
        print("  minimize - Minimize Mail windows")
        print("  closeall - Close all message windows")
        sys.exit(1)

    command = sys.argv[1]

    if command == "check" and len(sys.argv) >= 4:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        print(check_message_availability(message_id, mailbox_url))

    elif command == "download" and len(sys.argv) >= 4:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        print(trigger_message_download_silent(message_id, mailbox_url))

    elif command == "flag" and len(sys.argv) >= 5:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        color = sys.argv[4]
        print(set_message_flag(message_id, mailbox_url, color))

    elif command == "unflag" and len(sys.argv) >= 4:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        print(clear_message_flag(message_id, mailbox_url))

    elif command == "read" and len(sys.argv) >= 4:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        print(set_message_read_status(message_id, mailbox_url, True))

    elif command == "unread" and len(sys.argv) >= 4:
        message_id = int(sys.argv[2])
        mailbox_url = sys.argv[3]
        print(set_message_read_status(message_id, mailbox_url, False))

    elif command == "minimize":
        print(minimize_mail())

    elif command == "closeall":
        print(close_all_message_windows())

    else:
        print(f"Unknown command or missing arguments: {command}")
        sys.exit(1)
