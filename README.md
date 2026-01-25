<p align="center">
  <img src="errol.png" alt="Errol-Mail Logo" width="400">
</p>

<h1 align="center">Errol-Mail</h1>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+"></a>
  <a href="https://www.apple.com/macos/"><img src="https://img.shields.io/badge/macOS-12%2B-lightgrey.svg" alt="macOS 12+"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

An MCP (Model Context Protocol) server that provides AI assistants with comprehensive access to Apple Mail. Like Errol delivering letters to the Burrow, this tool fetches your emails for Claude-though possibly with fewer crash landings (???)

## Features

### 📬 Email Discovery
- **Browse mailboxes** - List all folders across all accounts
- **Search messages** - Filter by subject, sender, date range
- **Unread/flagged** - Quick access to messages needing attention
- **7 flag colors** - Filter by red, orange, yellow, green, blue, purple, gray
- **Custom flag labels** - Use your Mail.app names ("Action needed", "Waiting")

### 📖 Email Reading
- **Full content** - Read complete emails with headers and body
- **Thread view** - Read entire conversations chronologically
- **Smart quotes** - Automatic removal of redundant quoted text
- **Thread summaries** - Quick overview before diving deep

### 📎 Attachments
- **List attachments** - See all files in an email
- **Extract files** - Save attachments to temp directory
- **Batch extract** - Get all attachments at once
- **Auto cleanup** - Remove old extracted files

### ✏️ Message Management (Headless)
- **Read/unread status** - Mark messages without opening Mail
- **Flag colors** - Set/clear flags programmatically
- **Server sync** - Changes sync to IMAP/Exchange

### 📡 Server-Side Messages
- **Availability check** - Detect server-only emails
- **Silent download** - Fetch emails without UI disruption
- **Window cleanup** - Close message windows after batch ops

## Quick Start

### 1. Prerequisites

- **macOS 12.0+** (Monterey or later)
- **Python 3.9+**
- **Apple Mail** configured with at least one account

### 2. Grant Full Disk Access

Errol reads Mail's database, which requires Full Disk Access. Run the diagnostic script to check your setup:

```bash
python3 check_fda.py
```

If access is working, you'll see:
```
✅ Successfully accessed Mail database (12,345 messages)
```

If not, the script tells you **exactly what path to add** to Full Disk Access. This is important because:
- macOS permissions apply to the *actual binary*, not symlinks
- Homebrew Python uses wrapper scripts that point to `Python.app`
- MCP hosts spawn Python as a child process, so adding the host app isn't enough

The script handles all of this and gives you the precise path to add.

<details>
<summary><b>Manual Setup (if you prefer)</b></summary>

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Click **+** and add:
   - **Terminal users**: Your terminal app (Terminal.app, iTerm2, Warp)
   - **Homebrew Python**: The `Python.app` inside Cellar (run `check_fda.py` to find it)
   - **System Python**: `/usr/bin/python3`
3. Restart your terminal or MCP client

</details>

### 3. Install

```bash
git clone https://github.com/jasondk/errol-mail.git
cd errol-mail
pip install -r requirements.txt
```

### 4. Configure MCP Client

**For Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "errol": {
      "command": "python",
      "args": ["/absolute/path/to/errol-mail/server.py"]
    }
  }
}
```

**For Claude Code** (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "errol": {
      "command": "python",
      "args": ["/absolute/path/to/errol-mail/server.py"]
    }
  }
}
```

### 5. Test

```bash
python server.py --test
```

## Available Tools

### Discovery Tools

| Tool | Description |
|------|-------------|
| `list_mailboxes()` | List all mailboxes with message counts |
| `find_mailbox(term)` | Find mailboxes by name |
| `get_recent_messages(limit)` | Recent messages from all folders |
| `get_unread_messages(limit)` | Unread messages only |
| `get_folder_messages(folder, limit)` | Messages in specific folder |
| `search_messages(subject, sender, days_back)` | Search with filters |
| `get_flagged_messages(color, folder)` | Filter by any of 7 flag colors |

### Reading Tools

| Tool | Description |
|------|-------------|
| `read_email(message_id)` | Full email content |
| `read_thread(message_id)` | Entire conversation |
| `get_thread_summary(message_id)` | Thread overview |

### Attachment Tools

| Tool | Description |
|------|-------------|
| `list_attachments(message_id)` | List email attachments |
| `get_attachment(message_id, filename)` | Extract one file |
| `extract_all_message_attachments(message_id)` | Extract all files |
| `cleanup_attachments(hours)` | Remove old extracts |

### Message Modification Tools

| Tool | Description | Opens Window |
|------|-------------|--------------|
| `mark_email_read(message_id)` | Mark as read | No |
| `mark_email_unread(message_id)` | Mark as unread | No |
| `set_email_flag(message_id, color)` | Set flag color | No |
| `clear_email_flag(message_id)` | Remove flag | No |

### Server-Side Tools

| Tool | Description | Opens Window |
|------|-------------|--------------|
| `check_email_availability(message_id)` | Check if local or server-only | No |
| `download_email(message_id)` | Download from server | Yes |
| `download_email_silent(message_id)` | Download and auto-close | Briefly |
| `open_email_in_mail(message_id)` | Open in Mail.app | Yes |

### Utility Tools

| Tool | Description |
|------|-------------|
| `list_flag_colors()` | Show custom flag labels |
| `cleanup_mail_windows()` | Close all message windows |
| `minimize_mail_app()` | Minimize Mail to dock |

## Usage Examples

### Check what needs attention

```
"What unread emails do I have?"
"Show me flagged messages from the last week"
"Are there any emails from John about the project?"
```

### Read and triage emails

```
"Read the thread about the budget proposal"
"Summarize the conversation with the marketing team"
"Mark message 698914 as read and flag it red"
```

### Work with flagged messages

```
"Show me all my red-flagged action items"
"What orange messages am I waiting on?"
"Flag this email blue for reference"
"List flag colors to see my custom labels"
```

### Process attachments

```
"What attachments are in the email about Q4 reports?"
"Extract the PDF from message 698519"
```

### Handle server-only messages

```
"Check if message 279406 is downloaded"
"Download old emails from 2020 silently"
```

## Claude Skill

A Claude Code skill is included that teaches Claude how to effectively use Errol for email tasks. The skill provides workflow patterns, task templates, and complete API documentation.

### Installing the Skill

From the errol-mail directory, run:

```bash
mkdir -p ~/.claude/skills/errol && unzip -o errol.skill -d ~/.claude/skills/errol
```

### Skill Contents

```
~/.claude/skills/errol/
├── SKILL.md                    # Workflow patterns and quick reference
└── references/
    └── api_reference.md        # Complete API documentation
```

### What the Skill Provides

- **Trigger phrases** - Claude automatically recognizes "check my email", "what's in my inbox", etc.
- **Workflow patterns** - Discover → Summarize → Deep Dive approach for email triage
- **Task templates** - Common patterns for searching, reading threads, processing attachments
- **Complete API reference** - Every tool with parameters, examples, and return formats

### Customizing the Skill

The skill is a great place to add your own email automation workflows. Edit `~/.claude/skills/errol/SKILL.md` to add custom patterns:

```markdown
## My Custom Workflows

### Morning Email Triage
1. Check unread messages: `get_unread_messages(limit=20)`
2. Flag urgent items red: `set_email_flag(message_id, "red")`
3. Mark newsletters as read: `mark_email_read(message_id)`

### Weekly Report Processing
1. Search for reports: `search_messages(subject="weekly report", days_back=7)`
2. Extract attachments: `extract_all_message_attachments(message_id)`
3. Flag as processed: `set_email_flag(message_id, "green")`
```

Claude will learn your patterns and apply them when you ask for help with similar tasks.

## Architecture

```
errol-mail/
├── server.py                 # MCP server (FastMCP)
├── mail_cli.py               # Command-line interface
├── src/
│   ├── database.py           # SQLite database access
│   ├── messages.py           # Message queries and search
│   ├── email_reader.py       # Parse .emlx files
│   ├── threads.py            # Thread/conversation handling
│   ├── attachments.py        # Attachment extraction
│   └── applescript_helper.py # AppleScript integration
├── requirements.txt
└── errol.skill               # Packaged Claude skill
```

## How It Works

### Database Access
Apple Mail stores metadata in `~/Library/Mail/V10/MailData/Envelope Index` (SQLite). This includes subjects, senders, dates, flags, and conversation groupings.

### Email Content
Full email content is stored as `.emlx` files in nested directories under `~/Library/Mail/V10/`. The MCP server locates and parses these files on demand.

### AppleScript Integration
For operations requiring Mail.app interaction (downloads, flag changes), the server uses AppleScript. Most operations run headless without disrupting the UI.

### Server-Only Messages
Older emails may exist in the database but not be downloaded locally. The server can detect these and trigger downloads via AppleScript.

## Limitations

- **Read-only database** - Cannot compose, send, or delete emails
- **Local emails only** - Server-only emails require download first
- **macOS only** - Requires Apple Mail.app
- **Full Disk Access** - Required for database access

## Comparison to mail-mcp-bridge

Inspired by [fatbobman/mail-mcp-bridge](https://github.com/fatbobman/mail-mcp-bridge), this project adds:

| Feature | mail-mcp-bridge | Errol |
|---------|-----------------|-------|
| Browse mailboxes | ✗ | ✓ |
| Search messages | ✗ | ✓ |
| Unread/flagged filters | ✗ | ✓ |
| Custom flag labels | ✗ | ✓ |
| Thread summaries | ✗ | ✓ |
| Mark read/unread | ✗ | ✓ |
| Set/clear flags | ✗ | ✓ |
| Server-only detection | ✗ | ✓ |
| Silent downloads | ✗ | ✓ |
| Window management | ✗ | ✓ |
| Claude skill | ✗ | ✓ |

## Troubleshooting

### "Cannot access Mail database" errors

Run the diagnostic script first:
```bash
python3 check_fda.py
```

If it works in terminal but fails in Claude Desktop, the MCP host is spawning a different Python than the one you tested.

<details>
<summary><b>Why Full Disk Access is tricky with MCP servers</b></summary>

macOS FDA permissions are checked on the **exact binary that executes**, not on:
- Parent processes that spawn it
- Symlinks pointing to it
- App bundles (unless launched via Finder)

When Claude Desktop runs your MCP server:
```
Claude Desktop → spawns → Python → runs errol
```

FDA on Claude Desktop doesn't help Python. You need FDA on the actual Python binary.

**Homebrew Python** adds complexity: `/opt/homebrew/bin/python3` is a symlink → to a wrapper → that launches `Python.app`. FDA must be on that final `Python.app`.

The `check_fda.py` script finds this path for you.

</details>

<details>
<summary><b>Common pitfalls</b></summary>

| What you tried | Why it doesn't work |
|----------------|---------------------|
| Added `/opt/homebrew/bin/python3` | That's a symlink, not the binary |
| Added Claude.app | FDA doesn't propagate to child processes |
| Added your venv's python | venv python is a symlink to the real binary |

</details>

<details>
<summary><b>Still stuck?</b></summary>

1. **Check which Python is actually running:**
   ```bash
   ps aux | grep -i errol
   ```
   The binary shown must be in your FDA list.

2. **Check MCP logs:**
   ```bash
   # Claude Desktop logs:
   cat ~/Library/Logs/Claude/mcp*.log | grep -i errol
   ```

3. **Open an issue** on the GitHub repo with the output of `check_fda.py` and we'll help debug.

</details>

## Contributing

Contributions welcome! Please read the existing code style and add tests for new features.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- Inspired by [fatbobman/mail-mcp-bridge](https://github.com/fatbobman/mail-mcp-bridge)
- Built with [FastMCP](https://github.com/jlowin/fastmcp)
