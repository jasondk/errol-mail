#!/bin/bash
# Flag Color Storage Investigation Script
#
# This script helps identify where Apple Mail stores flag colors for Exchange accounts.
#
# Usage:
#   1. ./flag-color-experiment.sh snapshot before
#   2. Change a flag color in Mail.app
#   3. Quit Mail.app, wait 10 seconds
#   4. ./flag-color-experiment.sh snapshot after
#   5. ./flag-color-experiment.sh diff
#   6. ./flag-color-experiment.sh monitor  (run while changing flag)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK_DIR="$SCRIPT_DIR/flag-experiment-data"
MAIL_DATA="$HOME/Library/Mail"
MAIL_CONTAINER="$HOME/Library/Containers/com.apple.mail/Data/Library"
MAIL_GROUP="$HOME/Library/Group Containers"

# Target message for reference
TARGET_MSG_ID="698997"
TARGET_SUBJECT="Order confirmed: Mac Studio"

case "${1:-help}" in
    snapshot)
        SNAPSHOT_NAME="${2:-snapshot}"
        SNAPSHOT_DIR="$WORK_DIR/$SNAPSHOT_NAME"

        echo "Creating snapshot: $SNAPSHOT_NAME"
        mkdir -p "$SNAPSHOT_DIR"

        # Check if Mail is running
        if pgrep -x "Mail" > /dev/null; then
            echo "WARNING: Mail.app is still running. Quit it first for accurate snapshot."
            echo "Press Enter to continue anyway, or Ctrl+C to abort..."
            read
        fi

        echo "Capturing file listing and mtimes..."

        # Mail data directory
        if [ -d "$MAIL_DATA" ]; then
            find "$MAIL_DATA" -type f \( -name "*.sqlite*" -o -name "*.db" -o -name "*.plist" -o -name "*.storedata" -o -name "Envelope*" -o -name "*.wal" -o -name "*.shm" \) \
                -exec stat -f "%m %N" {} \; 2>/dev/null | sort > "$SNAPSHOT_DIR/mail-data-files.txt"
            echo "  - Mail data: $(wc -l < "$SNAPSHOT_DIR/mail-data-files.txt") files"
        fi

        # Mail container
        if [ -d "$MAIL_CONTAINER" ]; then
            find "$MAIL_CONTAINER" -type f \( -name "*.sqlite*" -o -name "*.db" -o -name "*.plist" -o -name "*.storedata" -o -name "*.cache" \) \
                -exec stat -f "%m %N" {} \; 2>/dev/null | sort > "$SNAPSHOT_DIR/mail-container-files.txt"
            echo "  - Mail container: $(wc -l < "$SNAPSHOT_DIR/mail-container-files.txt") files"
        fi

        # Group containers (Mail-related)
        if [ -d "$MAIL_GROUP" ]; then
            find "$MAIL_GROUP" -type f \( -name "*.sqlite*" -o -name "*.db" -o -name "*.plist" -o -name "*.storedata" \) 2>/dev/null \
                -exec stat -f "%m %N" {} \; 2>/dev/null | sort > "$SNAPSHOT_DIR/group-container-files.txt"
            echo "  - Group containers: $(wc -l < "$SNAPSHOT_DIR/group-container-files.txt") files"
        fi

        # Also capture the Envelope Index database state
        ENVELOPE_IDX="$MAIL_DATA/V10/MailData/Envelope Index"
        if [ -f "$ENVELOPE_IDX" ]; then
            echo "Capturing Envelope Index state..."
            sqlite3 "$ENVELOPE_IDX" "SELECT ROWID, flag_color, flags FROM messages WHERE ROWID = $TARGET_MSG_ID" > "$SNAPSHOT_DIR/target-msg-state.txt" 2>/dev/null || true
            sqlite3 "$ENVELOPE_IDX" "SELECT * FROM server_messages WHERE message = $TARGET_MSG_ID" >> "$SNAPSHOT_DIR/target-msg-state.txt" 2>/dev/null || true
            cat "$SNAPSHOT_DIR/target-msg-state.txt"
        fi

        echo ""
        echo "Snapshot '$SNAPSHOT_NAME' complete: $SNAPSHOT_DIR"
        ;;

    diff)
        BEFORE="$WORK_DIR/before"
        AFTER="$WORK_DIR/after"

        if [ ! -d "$BEFORE" ] || [ ! -d "$AFTER" ]; then
            echo "ERROR: Need both 'before' and 'after' snapshots."
            echo "Run: $0 snapshot before"
            echo "Then: $0 snapshot after"
            exit 1
        fi

        echo "=== FILES THAT CHANGED ==="
        echo ""

        for category in mail-data mail-container group-container; do
            BEFORE_FILE="$BEFORE/${category}-files.txt"
            AFTER_FILE="$AFTER/${category}-files.txt"

            if [ -f "$BEFORE_FILE" ] && [ -f "$AFTER_FILE" ]; then
                echo "--- $category ---"
                # Compare mtimes - show files where mtime changed
                diff "$BEFORE_FILE" "$AFTER_FILE" 2>/dev/null | grep "^[<>]" | sed 's/^[<>] //' | awk '{print $2}' | sort -u || true
                echo ""
            fi
        done

        echo "=== TARGET MESSAGE STATE DIFF ==="
        diff "$BEFORE/target-msg-state.txt" "$AFTER/target-msg-state.txt" 2>/dev/null || echo "(no change in Envelope Index)"
        ;;

    monitor)
        echo "Monitoring file writes by Mail.app..."
        echo "Start Mail, change a flag color, then quit Mail."
        echo "Press Ctrl+C to stop monitoring."
        echo ""
        echo "Filtering for: Mail-related paths"
        echo "=========================================="

        # Use fs_usage to monitor Mail file writes
        # Requires sudo
        sudo fs_usage -w -f filesys Mail 2>&1 | grep -E "(open|write|WrData|close)" | grep -E "(Library/Mail|com.apple.mail)" || true
        ;;

    inspect)
        # Inspect a specific file for flag-related data
        FILE="$2"
        if [ -z "$FILE" ]; then
            echo "Usage: $0 inspect <filepath>"
            exit 1
        fi

        echo "Inspecting: $FILE"
        echo ""

        # Determine file type
        FILE_TYPE=$(file "$FILE")
        echo "Type: $FILE_TYPE"
        echo ""

        if echo "$FILE_TYPE" | grep -q "SQLite"; then
            echo "=== SQLite Schema ==="
            sqlite3 "$FILE" ".schema" 2>/dev/null | head -100
            echo ""
            echo "=== Tables ==="
            sqlite3 "$FILE" ".tables" 2>/dev/null
            echo ""
            echo "=== Searching for target message (ROWID $TARGET_MSG_ID or subject) ==="
            # Try to find our target message
            for table in $(sqlite3 "$FILE" ".tables" 2>/dev/null); do
                RESULT=$(sqlite3 "$FILE" "SELECT * FROM $table LIMIT 1" 2>/dev/null | head -1)
                if [ -n "$RESULT" ]; then
                    # Check if table might have our message
                    sqlite3 "$FILE" "SELECT '$table:', * FROM $table WHERE CAST(ROWID AS TEXT) LIKE '%$TARGET_MSG_ID%' OR CAST(* AS TEXT) LIKE '%Mac Studio%' LIMIT 5" 2>/dev/null || true
                fi
            done
        elif echo "$FILE_TYPE" | grep -q "plist"; then
            echo "=== Plist Contents ==="
            plutil -p "$FILE" 2>/dev/null | head -200
        else
            echo "=== Strings containing target subject ==="
            strings "$FILE" 2>/dev/null | grep -i "Mac Studio" | head -20
            echo ""
            echo "=== Strings containing 'flag' ==="
            strings "$FILE" 2>/dev/null | grep -i "flag" | head -20
        fi
        ;;

    help|*)
        echo "Flag Color Storage Investigation Script"
        echo ""
        echo "Target message for experiment:"
        echo "  ROWID: $TARGET_MSG_ID"
        echo "  Subject: $TARGET_SUBJECT"
        echo ""
        echo "Commands:"
        echo "  $0 snapshot <name>  - Capture file state (use 'before' and 'after')"
        echo "  $0 diff             - Compare before/after snapshots"
        echo "  $0 monitor          - Monitor Mail file writes in real-time (requires sudo)"
        echo "  $0 inspect <file>   - Inspect a specific file for flag data"
        echo ""
        echo "Workflow:"
        echo "  1. Quit Mail.app"
        echo "  2. $0 snapshot before"
        echo "  3. Start Mail, set the target message flag to BLUE"
        echo "  4. Quit Mail, wait 10 seconds"
        echo "  5. $0 snapshot after"
        echo "  6. Start Mail, change flag to ORANGE"
        echo "  7. Quit Mail, wait 10 seconds"
        echo "  8. $0 snapshot after2  (or just run diff with current 'after')"
        echo "  9. $0 diff"
        echo ""
        echo "Alternative: Real-time monitoring"
        echo "  1. $0 monitor  (in one terminal)"
        echo "  2. Start Mail, change flag color, quit Mail"
        echo "  3. Review output to see which files were written"
        ;;
esac
