"""Apple Mail MCP - Enhanced Apple Mail access via MCP protocol"""

try:
    # When imported as a package
    from .database import MailDatabase, MailDatabaseError
    from .messages import MessageQuery
    from .email_reader import parse_emlx_file
    from .threads import ThreadQuery
    from .attachments import list_attachments, extract_attachment, extract_all_attachments
except ImportError:
    # When run directly with src in path
    from database import MailDatabase, MailDatabaseError
    from messages import MessageQuery
    from email_reader import parse_emlx_file
    from threads import ThreadQuery
    from attachments import list_attachments, extract_attachment, extract_all_attachments

__all__ = [
    "MailDatabase",
    "MailDatabaseError",
    "MessageQuery",
    "parse_emlx_file",
    "ThreadQuery",
    "list_attachments",
    "extract_attachment",
    "extract_all_attachments",
]
