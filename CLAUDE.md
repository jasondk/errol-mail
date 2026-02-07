# CLAUDE.md — Errol-Mail

## Project Overview

Errol-Mail is a Python MCP (Model Context Protocol) server that gives AI assistants comprehensive read-only access to Apple Mail's local database. It bridges Claude and Mail.app via three data sources: SQLite (metadata), .emlx files (content), and AppleScript (headless operations).

## Architecture

```
server.py                    # MCP server — 26 tools exposed via FastMCP
src/
  database.py                # SQLite read-only access to Envelope Index
  messages.py                # Message queries (recent, search, folder)
  email_reader.py            # .emlx parsing + prompt injection detection
  threads.py                 # Thread/conversation grouping
  attachments.py             # Attachment extraction to temp dir
  applescript_helper.py      # Headless flag/read/download via osascript
mail_cli.py                  # CLI interface for manual testing
check_fda.py                 # Full Disk Access diagnostic tool
docs/
  SKILL.md                   # AI skill documentation (source of truth)
  api_reference.md           # Complete API reference
errol.skill                  # Packaged skill (zip of SKILL.md + api_reference.md)
```

## Key Data Sources

- **SQLite DB**: `~/Library/Mail/V10/MailData/Envelope Index` — message metadata, opened read-only via URI mode
- **.emlx files**: `~/Library/Mail/V10/{account-uuid}/.../{rowid}.emlx` — full email content, located via pathlib.rglob
- **AppleScript**: headless operations (flag, mark read, download) — runs silently without opening Mail UI

## Development Commands

```bash
python server.py --test          # Smoke tests (mailboxes, recent, unread)
python server.py --logs          # Show recent log entries
python server.py --tail          # Follow logs in real-time
python mail_cli.py mailboxes     # CLI: list mailboxes
python check_fda.py              # Diagnose Full Disk Access
```

Logs go to `~/Library/Logs/errol-mail/server.log`.

## Key Design Decisions

### Security Model (Prompt Injection Defense)
Email content is untrusted input that becomes part of the LLM context. Defenses:
1. **Content isolation** — bodies wrapped in `<email-content source="untrusted">` tags
2. **Security header** — constant reminder on all reads
3. **Injection detection** — 18 regex patterns in `email_reader.py` scan for common attacks
4. **Markdown escaping** — subjects/senders escaped to prevent table-breaking
5. **Junk/Trash exclusion** — discovery tools exclude Junk/Spam/Trash by default to reduce exposure to malicious content

### Junk/Trash Folder Safety
`DEFAULT_EXCLUDED_FOLDERS` in server.py defines folder patterns (Junk, Trash, Spam, Deleted, Bin) excluded from discovery tools by default. The `include_junk_trash` parameter (default False) must be explicitly set to True to include these. This is a security measure — junk mail is more likely to contain prompt injection attempts. The skill documentation instructs LLMs to only include junk/trash when the user explicitly requests it.

### Performance
- Subquery pattern in `get_recent_messages` selects ROWIDs first, then joins (7000x faster)
- `_find_email_files_batch()` does single directory traversal for multiple emails (57x faster)
- `read_emails_batch()` uses ThreadPoolExecutor for parallel .emlx parsing

### Headless AppleScript
Flag/read changes use AppleScript with plist modification rather than UI scripting. No windows open, Mail stays in background.

### Flag Color Storage
Flag colors are encoded in bits 39-41 of the `flags` integer in the messages table. Values 0-6 map to colors 1-7 (Red through Gray). Custom label names are read from `com.apple.mail` preferences via `defaults` command.

## Skill Packaging

The `.skill` file is a zip archive containing `SKILL.md` and `references/api_reference.md`. After changing `docs/SKILL.md`, rebuild with:
```bash
cd docs && zip -r ../errol.skill SKILL.md references/ assets/ scripts/
```

Install for Claude Code:
```bash
mkdir -p ~/.claude/skills/errol && unzip -o errol.skill -d ~/.claude/skills/errol
```

## Common Patterns

- **Tool decorator chain**: `@mcp.tool()` then `@log_tool_call` — order matters
- **DB access**: Always use `with db.connection() as conn:` context manager (read-only, auto-close)
- **Message formatting**: `_format_messages()` handles markdown table output with escaping
- **File lookup**: `_find_email_file()` for single, `_find_email_files_batch()` for multiple
- **Error pattern**: Catch `MailDatabaseError` for DB issues, return user-friendly strings
