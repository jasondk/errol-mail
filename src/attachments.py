#!/usr/bin/env python3
"""
Apple Mail Attachment Handling

Extract and access email attachments from .emlx files.
"""

import email
import email.policy
from email.header import decode_header
from pathlib import Path
from typing import Dict, List, Any, Optional
import os
import shutil
import tempfile


def decode_header_value(value: str) -> str:
    """Decode encoded email header values"""
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


def get_attachment_dir() -> str:
    """
    Get the directory for extracted attachments.

    Returns:
        Path to attachment directory (creates if needed)
    """
    base_dir = os.environ.get('MAIL_ATTACHMENT_PATH', tempfile.gettempdir())
    att_dir = os.path.join(base_dir, 'apple-mail-mcp-attachments')
    os.makedirs(att_dir, exist_ok=True)
    return att_dir


def list_attachments(file_path: str) -> Dict[str, Any]:
    """
    List all attachments in an .emlx file.

    Args:
        file_path: Path to .emlx file

    Returns:
        Dictionary with attachment list
    """
    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}"
        }

    try:
        # Read and parse .emlx
        with open(file_path_obj, 'rb') as f:
            lines = f.readlines()

        if len(lines) < 2:
            return {
                "success": False,
                "error": "Invalid file format"
            }

        # Extract email content
        email_lines = []
        for line in lines[1:]:
            line_str = line.decode('utf-8', errors='ignore')
            if '<?xml version' in line_str or '<!DOCTYPE plist' in line_str or '<plist version' in line_str:
                break
            email_lines.append(line)

        raw_content = b''.join(email_lines)
        msg = email.message_from_bytes(raw_content, policy=email.policy.compat32)

        # Find attachments
        attachments = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get('Content-Disposition', ''))
            filename = part.get_filename()

            is_attachment = (
                'attachment' in content_disposition or
                (filename and content_type not in ['text/plain', 'text/html'])
            )

            if is_attachment and filename:
                filename = decode_header_value(filename)
                payload = part.get_payload(decode=True)
                size_bytes = len(payload) if payload else 0

                # Check for externally stored attachment if size is 0
                if size_bytes == 0:
                    size_bytes = _get_external_attachment_size(file_path_obj, filename)

                attachments.append({
                    "filename": filename,
                    "mime_type": content_type,
                    "size_bytes": size_bytes,
                    "inline": 'inline' in content_disposition
                })

        return {
            "success": True,
            "file_path": file_path,
            "attachment_count": len(attachments),
            "attachments": attachments
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to list attachments: {str(e)}"
        }


def extract_attachment(
    file_path: str,
    filename: str,
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract a specific attachment from an .emlx file.

    Args:
        file_path: Path to .emlx file
        filename: Name of the attachment to extract
        output_dir: Optional output directory (defaults to temp dir)

    Returns:
        Dictionary with extraction result
    """
    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}"
        }

    try:
        # Read and parse .emlx
        with open(file_path_obj, 'rb') as f:
            lines = f.readlines()

        if len(lines) < 2:
            return {
                "success": False,
                "error": "Invalid file format"
            }

        email_lines = []
        for line in lines[1:]:
            line_str = line.decode('utf-8', errors='ignore')
            if '<?xml version' in line_str or '<!DOCTYPE plist' in line_str or '<plist version' in line_str:
                break
            email_lines.append(line)

        raw_content = b''.join(email_lines)
        msg = email.message_from_bytes(raw_content, policy=email.policy.compat32)

        # Find the requested attachment
        for part in msg.walk():
            part_filename = part.get_filename()
            if part_filename:
                part_filename = decode_header_value(part_filename)

                if part_filename == filename:
                    payload = part.get_payload(decode=True)

                    if not payload:
                        # Try to find in file system (Mail stores large attachments separately)
                        external_path = _find_external_attachment(file_path_obj, filename)
                        if external_path:
                            payload = external_path.read_bytes()

                    if payload:
                        # Save to output directory
                        out_dir = output_dir or get_attachment_dir()
                        os.makedirs(out_dir, exist_ok=True)

                        # Sanitize filename
                        safe_filename = filename.replace('/', '_').replace('\\', '_').replace(':', '_')
                        output_path = Path(out_dir) / safe_filename

                        with open(output_path, 'wb') as f:
                            f.write(payload)

                        return {
                            "success": True,
                            "filename": filename,
                            "output_path": str(output_path),
                            "mime_type": part.get_content_type(),
                            "size_bytes": len(payload)
                        }

        return {
            "success": False,
            "error": f"Attachment not found: {filename}"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to extract attachment: {str(e)}"
        }


def extract_all_attachments(
    file_path: str,
    output_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Extract all attachments from an .emlx file.

    Args:
        file_path: Path to .emlx file
        output_dir: Optional output directory

    Returns:
        Dictionary with extraction results
    """
    file_path_obj = Path(file_path)

    if not file_path_obj.exists():
        return {
            "success": False,
            "error": f"File not found: {file_path}"
        }

    try:
        # Read and parse .emlx
        with open(file_path_obj, 'rb') as f:
            lines = f.readlines()

        if len(lines) < 2:
            return {
                "success": False,
                "error": "Invalid file format"
            }

        email_lines = []
        for line in lines[1:]:
            line_str = line.decode('utf-8', errors='ignore')
            if '<?xml version' in line_str or '<!DOCTYPE plist' in line_str or '<plist version' in line_str:
                break
            email_lines.append(line)

        raw_content = b''.join(email_lines)
        msg = email.message_from_bytes(raw_content, policy=email.policy.compat32)

        # Setup output directory
        out_dir = output_dir or get_attachment_dir()

        # Create message-specific subdirectory
        message_dir = Path(out_dir) / file_path_obj.stem
        message_dir.mkdir(parents=True, exist_ok=True)

        extracted = []
        failed = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get('Content-Disposition', ''))
            filename = part.get_filename()

            is_attachment = (
                'attachment' in content_disposition or
                (filename and content_type not in ['text/plain', 'text/html'])
            )

            if is_attachment and filename:
                filename = decode_header_value(filename)
                payload = part.get_payload(decode=True)

                if not payload:
                    # Try external storage
                    external_path = _find_external_attachment(file_path_obj, filename)
                    if external_path:
                        payload = external_path.read_bytes()

                if payload:
                    safe_filename = filename.replace('/', '_').replace('\\', '_').replace(':', '_')
                    output_path = message_dir / safe_filename

                    with open(output_path, 'wb') as f:
                        f.write(payload)

                    extracted.append({
                        "filename": filename,
                        "output_path": str(output_path),
                        "mime_type": content_type,
                        "size_bytes": len(payload)
                    })
                else:
                    failed.append({
                        "filename": filename,
                        "error": "Could not retrieve content"
                    })

        return {
            "success": True,
            "output_dir": str(message_dir),
            "extracted_count": len(extracted),
            "extracted": extracted,
            "failed": failed if failed else None
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to extract attachments: {str(e)}"
        }


def _find_external_attachment(emlx_path: Path, filename: str) -> Optional[Path]:
    """
    Find an attachment stored externally by Mail.app.

    Mail stores large attachments in a separate Attachments directory.
    Structure: .../Messages/{rowid}.emlx -> .../Attachments/{rowid}/{index}/{filename}
    The Attachments dir is a sibling to the Messages dir.
    """
    try:
        messages_dir = emlx_path.parent  # .../Messages/
        parent_dir = messages_dir.parent  # .../{X}/
        attachments_dir = parent_dir / "Attachments"
        message_num = emlx_path.stem.replace('.partial', '')

        if attachments_dir.exists():
            message_att_dir = attachments_dir / message_num
            if message_att_dir.exists():
                # Search in numbered subdirectories
                for sub_dir in message_att_dir.iterdir():
                    if sub_dir.is_dir():
                        # Try exact match
                        potential_file = sub_dir / filename
                        if potential_file.exists():
                            return potential_file

                        # Try with sanitized name
                        alt_filename = filename.replace('/', '_').replace('\\', '_')
                        potential_file = sub_dir / alt_filename
                        if potential_file.exists():
                            return potential_file

        return None

    except Exception:
        return None


def _get_external_attachment_size(emlx_path: Path, filename: str) -> int:
    """Get the size of an externally stored attachment."""
    external_path = _find_external_attachment(emlx_path, filename)
    if external_path and external_path.exists():
        return external_path.stat().st_size
    return 0


def cleanup_extracted_attachments(older_than_hours: int = 24) -> Dict[str, Any]:
    """
    Clean up old extracted attachments.

    Args:
        older_than_hours: Delete files older than this many hours

    Returns:
        Cleanup result
    """
    import time

    att_dir = get_attachment_dir()

    if not os.path.exists(att_dir):
        return {
            "success": True,
            "deleted_count": 0,
            "message": "Attachment directory does not exist"
        }

    try:
        cutoff = time.time() - (older_than_hours * 3600)
        deleted_count = 0
        deleted_bytes = 0

        for root, dirs, files in os.walk(att_dir, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                if os.path.getmtime(file_path) < cutoff:
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    deleted_count += 1
                    deleted_bytes += size

            # Remove empty directories
            for name in dirs:
                dir_path = os.path.join(root, name)
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)

        return {
            "success": True,
            "deleted_count": deleted_count,
            "deleted_bytes": deleted_bytes,
            "attachment_dir": att_dir
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Cleanup failed: {str(e)}"
        }


def main():
    """CLI test tool"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python attachments.py <emlx_file> [filename_to_extract]")
        sys.exit(1)

    file_path = sys.argv[1]

    if len(sys.argv) > 2:
        # Extract specific attachment
        filename = sys.argv[2]
        print(f"Extracting '{filename}' from {file_path}\n")

        result = extract_attachment(file_path, filename)
        if result["success"]:
            print(f"Extracted: {result['output_path']}")
            print(f"Size: {result['size_bytes']} bytes")
        else:
            print(f"Error: {result['error']}")
            sys.exit(1)
    else:
        # List attachments
        print(f"Listing attachments in {file_path}\n")

        result = list_attachments(file_path)
        if result["success"]:
            if result["attachments"]:
                print(f"Found {result['attachment_count']} attachment(s):\n")
                for att in result["attachments"]:
                    print(f"  - {att['filename']}")
                    print(f"    Type: {att['mime_type']}")
                    print(f"    Size: {att['size_bytes']} bytes")
                    print()
            else:
                print("No attachments found")
        else:
            print(f"Error: {result['error']}")
            sys.exit(1)


if __name__ == '__main__':
    main()
