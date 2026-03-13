#!/usr/bin/env python3
"""
Apple Mail Email Reader

Parse .emlx files and extract email content, headers, and attachment metadata.
Includes security features for prompt injection detection.
"""

import email
import email.policy
from email.header import decode_header
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Optional, Any, List
import plistlib
import re
import os


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text using stdlib HTMLParser."""

    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._pieces: list[str] = []
            self._skip = False
            self._block_tags = {'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}
            self._skip_tags = {'style', 'script', 'head'}

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._skip = True
            if tag in self._block_tags:
                self._pieces.append('\n')
            if tag == 'a':
                for name, value in attrs:
                    if name == 'href' and value:
                        self._pieces.append(f' [{value}] ')

        def handle_endtag(self, tag):
            if tag in self._skip_tags:
                self._skip = False
            if tag in self._block_tags:
                self._pieces.append('\n')

        def handle_data(self, data):
            if not self._skip:
                self._pieces.append(data)

    extractor = _Extractor()
    extractor.feed(html)
    text = ''.join(extractor._pieces)
    # Collapse runs of blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ============================================================================
# SECURITY: Prompt Injection Detection
# ============================================================================

# Common prompt injection patterns to detect
INJECTION_PATTERNS = [
    (r'ignore\s+(all\s+)?(previous|prior|above|earlier)?\s*instructions?', 'Attempt to override instructions'),
    (r'\[SYSTEM\]', 'Fake system message marker'),
    (r'\[ADMIN\]', 'Fake admin message marker'),
    (r'\[ASSISTANT\]', 'Fake assistant message marker'),
    (r'<\s*system\s*>', 'Fake XML system tag'),
    (r'you\s+are\s+now\s+(a\s+different|no\s+longer)', 'Attempt to change AI identity'),
    (r'execute\s+(this|the\s+following)\s+(code|command|script)', 'Command execution request'),
    (r'run\s+(this|the\s+following)\s+(code|command|script)', 'Command execution request'),
    (r'(reveal|share|output|print|display)\s+(your|the)\s+(system\s+prompt|instructions|api\s+key|secret|password|credential)', 'Credential/prompt extraction attempt'),
    (r'forget\s+(everything|all|what)\s+(you|about)', 'Memory manipulation attempt'),
    (r'new\s+session|reset\s+context', 'Context reset attempt'),
    (r'disregard\s+(all|any|previous)', 'Instruction override attempt'),
    (r'pretend\s+(you\s+are|to\s+be|that)', 'Role-playing injection'),
    (r"let's\s+play\s+a\s+game|roleplay\s+as", 'Jailbreak game attempt'),
]

# Compile patterns for efficiency
_COMPILED_INJECTION_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), description)
    for pattern, description in INJECTION_PATTERNS
]


def check_for_injection_patterns(text: str) -> List[str]:
    """
    Detect common prompt injection patterns in text.

    This is a defense-in-depth measure. It won't catch all attacks,
    but flags obvious attempts for human review.

    Args:
        text: Text to scan (typically email body)

    Returns:
        List of warning messages for detected patterns (empty if clean)
    """
    if not text:
        return []

    warnings = []
    for pattern, description in _COMPILED_INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            # Include a snippet of what was matched for context
            snippet = match.group(0)[:50]
            if len(match.group(0)) > 50:
                snippet += "..."
            warnings.append(f'{description}: "{snippet}"')

    return warnings


def get_emlx_flag_color(file_path: str) -> Optional[int]:
    """
    Extract flag color from emlx file's plist metadata.

    Apple Mail stores the flag color in bits 39-41 of the 'flags' integer
    in the plist metadata at the end of .emlx files.

    Color mapping (bits 39-41 value):
        0 = Red, 1 = Orange, 2 = Yellow, 3 = Green,
        4 = Blue, 5 = Purple, 6 = Gray

    Args:
        file_path: Path to the .emlx file

    Returns:
        Flag color as integer (0-6), or None if unable to read
    """
    try:
        with open(file_path, 'rb') as f:
            content = f.read()

        # Find plist section (starts with <?xml or <!DOCTYPE plist)
        plist_start = content.find(b'<?xml')
        if plist_start == -1:
            plist_start = content.find(b'<!DOCTYPE plist')
        if plist_start == -1:
            plist_start = content.find(b'<plist')

        if plist_start > 0:
            plist_data = content[plist_start:]
            metadata = plistlib.loads(plist_data)
            flags_int = metadata.get('flags', 0)
            # Flag color is stored in bits 39-41
            return (flags_int >> 39) & 0x7
    except Exception:
        pass

    return None


def decode_header_value(value: str) -> str:
    """
    Decode encoded email header values (e.g. =?utf-8?B?...?=)

    Args:
        value: Encoded header value

    Returns:
        Decoded string
    """
    if not value:
        return ""

    decoded_parts = []
    for content, encoding in decode_header(value):
        if isinstance(content, bytes):
            if encoding:
                try:
                    decoded_parts.append(content.decode(encoding))
                except (LookupError, UnicodeDecodeError):
                    decoded_parts.append(content.decode('utf-8', errors='replace'))
            else:
                decoded_parts.append(content.decode('utf-8', errors='replace'))
        else:
            decoded_parts.append(str(content))

    return ''.join(decoded_parts)


class QuoteStripper:
    """Intelligently strip quoted content from emails for thread reading"""

    # Quote markers (ordered by priority)
    QUOTE_PATTERNS = [
        (r'^>+\s?', 'prefix'),              # > quoted lines
        (r'^On .+wrote:$', 'header'),       # "On ... wrote:"
        (r'^On .+:$', 'header'),             # "On ...:"
        (r'^\d{4}年\d{1,2}月\d{1,2}日.+写道：', 'header'),  # Chinese
        (r'^From:\s', 'forward'),           # Forward markers
        (r'^Sent:\s', 'forward'),
        (r'^-{5,}\s*Original Message\s*-{5,}', 'separator'),
        (r'^_{10,}', 'separator'),
        (r'^={10,}', 'separator'),
    ]

    def __init__(self, keep_quote_lines: int = 5):
        """
        Initialize quote stripper

        Args:
            keep_quote_lines: Number of lines to keep from each quote block
        """
        self.keep_quote_lines = keep_quote_lines
        self.compiled_patterns = [
            (re.compile(pattern, re.MULTILINE | re.IGNORECASE), ptype)
            for pattern, ptype in self.QUOTE_PATTERNS
        ]

    def _is_quote_line(self, line: str) -> bool:
        """Check if a line is part of a quote"""
        stripped = line.strip()
        if not stripped:
            return False

        for pattern, _ in self.compiled_patterns:
            if pattern.match(line):
                return True

        return False

    def strip_quotes(self, text: str, max_length: int = 0) -> tuple:
        """
        Strip redundant quotes from email text

        Args:
            text: Email body text
            max_length: Maximum total length (0 = unlimited)

        Returns:
            Tuple of (stripped_text, metadata)
        """
        if not text:
            return text, {}

        original_length = len(text)
        lines = text.split('\n')
        result_lines = []
        in_quote_block = False
        quote_block_lines = 0
        quotes_stripped = 0

        for line in lines:
            is_quote = self._is_quote_line(line)

            if is_quote:
                if not in_quote_block:
                    # Starting a new quote block
                    in_quote_block = True
                    quote_block_lines = 0

                quote_block_lines += 1

                if quote_block_lines <= self.keep_quote_lines:
                    result_lines.append(line)
                else:
                    quotes_stripped += 1
            else:
                if in_quote_block and quotes_stripped > 0:
                    # End of quote block, add marker
                    result_lines.append(f'[... {quotes_stripped} quoted lines omitted ...]')
                    quotes_stripped = 0

                in_quote_block = False
                quote_block_lines = 0
                result_lines.append(line)

        # Handle trailing quote block
        if in_quote_block and quotes_stripped > 0:
            result_lines.append(f'[... {quotes_stripped} quoted lines omitted ...]')

        result = '\n'.join(result_lines)

        # Apply hard limit if needed
        hard_truncated = False
        if max_length > 0 and len(result) > max_length:
            result = result[:max_length] + '\n[... content truncated ...]'
            hard_truncated = True

        metadata = {
            'original_length': original_length,
            'stripped_length': len(result),
            'hard_truncated': hard_truncated
        }

        return result, metadata


def parse_emlx_file(
    file_path: str,
    max_body_length: int = 0,
    strip_quotes: bool = False
) -> Dict[str, Any]:
    """
    Parse .emlx file and extract email content

    Args:
        file_path: Absolute path to .emlx file
        max_body_length: Maximum body length in characters (0 = unlimited, default 10000)
        strip_quotes: Enable smart quote stripping for thread reading

    Returns:
        Dictionary containing email information:
        {
            "success": True/False,
            "message_id": "...",
            "subject": "...",
            "from": "...",
            "to": "...",
            "cc": "...",
            "date": "...",
            "body_text": "email body",
            "body_html": "html body if available",
            "attachments": [
                {
                    "filename": "...",
                    "mime_type": "...",
                    "size_bytes": 12345
                }
            ],
            "truncated": True/False,
            "error": "..." (if failed)
        }
    """
    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}"
        }

    # Default max body length
    if max_body_length == 0:
        max_body_length = int(os.environ.get('MAIL_MAX_BODY_LENGTH', '10000'))

    try:
        # Read email content
        with open(file_path_obj, 'rb') as f:
            lines = f.readlines()

        # .emlx file format:
        # First line: file size
        # Second line onwards: raw email content
        # Last few lines: Apple plist metadata (XML)

        if len(lines) < 2:
            return {
                "success": False,
                "error": "Invalid file format or empty file"
            }

        # From second line, find plist start position
        email_lines = []
        for line in lines[1:]:  # Skip first line (size)
            line_str = line.decode('utf-8', errors='ignore')
            # Detect plist start marker
            if '<?xml version' in line_str or '<!DOCTYPE plist' in line_str or '<plist version' in line_str:
                break
            email_lines.append(line)

        raw_content = b''.join(email_lines)

        # Parse email
        msg = email.message_from_bytes(raw_content, policy=email.policy.compat32)

        # Extract headers
        message_id = msg.get('Message-Id', '')
        subject = decode_header_value(msg.get('Subject', ''))
        from_addr = decode_header_value(msg.get('From', ''))
        to_addr = decode_header_value(msg.get('To', ''))
        cc_addr = decode_header_value(msg.get('Cc', ''))
        date = msg.get('Date', '')

        # Threading headers
        references = msg.get('References', '')
        in_reply_to = msg.get('In-Reply-To', '')

        # Extract attachments metadata and body
        attachments = []
        body_text = ""
        body_html = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))

                # Check for attachment
                is_attachment = (
                    'attachment' in content_disposition or
                    (part.get_filename() and content_type not in ['text/plain', 'text/html'])
                )

                if is_attachment:
                    filename = part.get_filename()
                    if filename:
                        filename = decode_header_value(filename)
                        payload = part.get_payload(decode=True)
                        size_bytes = len(payload) if payload else 0

                        attachments.append({
                            "filename": filename,
                            "mime_type": content_type,
                            "size_bytes": size_bytes
                        })

                elif content_type == 'text/plain' and not body_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        try:
                            body_text = payload.decode(charset)
                        except (UnicodeDecodeError, LookupError):
                            body_text = payload.decode('utf-8', errors='replace')

                elif content_type == 'text/html' and not body_html:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or 'utf-8'
                        try:
                            body_html = payload.decode(charset)
                        except (UnicodeDecodeError, LookupError):
                            body_html = payload.decode('utf-8', errors='replace')
        else:
            # Single part email
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                try:
                    decoded = payload.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    decoded = payload.decode('utf-8', errors='replace')

                if content_type == 'text/html':
                    body_html = decoded
                else:
                    body_text = decoded

        # Fallback: convert HTML to plain text when no text/plain part exists
        if not body_text.strip() and body_html:
            body_text = _html_to_text(body_html)

        # Process body text
        body_text = body_text.strip()
        original_length = len(body_text)
        truncated = False

        # Strip quotes if requested
        if strip_quotes and body_text:
            stripper = QuoteStripper(keep_quote_lines=5)
            body_text, quote_meta = stripper.strip_quotes(body_text, max_length=max_body_length)
            truncated = quote_meta.get('hard_truncated', False)
        elif max_body_length > 0 and original_length > max_body_length:
            body_text = body_text[:max_body_length] + '\n[... content truncated ...]'
            truncated = True

        result = {
            "success": True,
            "message_id": message_id,
            "subject": subject,
            "from": from_addr,
            "to": to_addr,
            "cc": cc_addr,
            "date": date,
            "references": references,
            "in_reply_to": in_reply_to,
            "body_text": body_text,
            "attachments": attachments
        }

        if body_html:
            result["body_html"] = body_html if len(body_html) < max_body_length else body_html[:max_body_length]

        if truncated:
            result["truncated"] = True
            result["original_length"] = original_length

        return result

    except Exception as e:
        return {
            "success": False,
            "error": f"Parse failed: {str(e)}"
        }


def main():
    """CLI test tool"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python email_reader.py <emlx_file_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    print(f"Parsing: {file_path}\n")

    result = parse_emlx_file(file_path)

    if result['success']:
        print(f"Subject: {result['subject']}")
        print(f"From: {result['from']}")
        print(f"To: {result['to']}")
        print(f"Date: {result['date']}")
        print(f"Message-ID: {result['message_id']}")

        if result['attachments']:
            print(f"\nAttachments ({len(result['attachments'])}):")
            for att in result['attachments']:
                print(f"  - {att['filename']} ({att['mime_type']}, {att['size_bytes']} bytes)")

        print(f"\nBody:\n{'-' * 60}")
        print(result['body_text'])
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)


if __name__ == '__main__':
    main()
