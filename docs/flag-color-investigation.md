# Apple Mail Flag Color Investigation

## Executive Summary

Flag color filtering in Errol works perfectly for **IMAP accounts** but has fundamental limitations for **Exchange/EWS accounts** due to how Apple Mail stores flag data differently for each protocol.

---

## Data Sources Investigated

### 1. `messages.flag_color` Column

**Location:** `messages` table in Envelope Index database

**Findings:**
- Always shows `1` (red) regardless of actual flag color
- Unreliable for determining current flag color
- Only indicates that a message IS flagged, not what color

### 2. `server_messages.flag_color` Column

**Location:** `server_messages` table in Envelope Index database

**Findings:**
- Contains accurate, current flag color (0-6 range)
- **Only populated for IMAP accounts** (100% coverage)
- **Never populated for Exchange/EWS accounts** (0% coverage)
- Values: 0=Red, 1=Orange, 2=Yellow, 3=Green, 4=Blue, 5=Purple, 6=Gray

**Evidence:**
```
Account Type    Total Flagged   Has server_messages   Percentage
----------------------------------------------------------------
Exchange        55              0                     0.0%
IMAP            16              16                    100.0%
```

### 3. `.emlx` File Plist Metadata

**Location:** `flags` field in plist at end of each `.emlx` file

**Findings:**
- Contains a large integer bitmask with various flags
- Bits 8-10 contain a color value (0-6 range, same as server_messages)
- **This value is CACHED and does NOT update when user changes flag color**
- Represents the flag color when the message was first flagged (or some historical state)

**Evidence of staleness:**
```
Comparing server_messages vs emlx for 16 IMAP messages:

Matches: 5
Mismatches: 11

Example mismatches (all show emlx=Blue when actual color differs):
  ID 695565: server=Orange, emlx=Blue
  ID 695919: server=Red, emlx=Blue
  ID 696308: server=Orange, emlx=Blue
  ID 697681: server=Red, emlx=Blue
```

### 4. `messages.flags` Column

**Location:** `messages` table in Envelope Index database

**Findings:**
- Contains the same bitmask value as the `.emlx` plist
- Bits 8-10 match the emlx file exactly
- Therefore also stale/cached, not current

### 5. `conversations.flags` Column

**Location:** `conversations` table

**Findings:**
- Always `0` for tested messages
- Does not contain flag color information

---

## Account Type Behavior

### IMAP Accounts (Gmail, standard IMAP)

| Data Source | Accuracy | Coverage |
|-------------|----------|----------|
| `server_messages.flag_color` | ✅ Accurate & Current | 100% |
| `messages.flag_color` | ❌ Always 1 | 100% |
| `emlx` bits 8-10 | ⚠️ Stale/Cached | 100% |

**Recommendation:** Use `server_messages.flag_color` - it's accurate and complete.

### Exchange/EWS Accounts

| Data Source | Accuracy | Coverage |
|-------------|----------|----------|
| `server_messages.flag_color` | N/A | 0% (no entries) |
| `messages.flag_color` | ❌ Always 1 | 100% |
| `emlx` bits 8-10 | ⚠️ Stale/Cached | 100% |

**Problem:** No reliable source for current flag color.

---

## The Two Approaches

### Approach 1: server_messages Only (Current in codebase after revert)

```python
# Use server_messages when available, fall back to messages.flag_color
COALESCE(sm.flag_color + 1, m.flag_color)
```

**Pros:**
- IMAP accounts get accurate colors
- Simple, fast query

**Cons:**
- Exchange accounts all show as red (since m.flag_color = 1)
- ~80% of user's flagged messages show wrong color

**Results:**
- Red: 61 messages (mostly wrong - Exchange messages defaulting to red)
- Orange: 6 messages (correct - IMAP)
- Blue: 4 messages (correct - IMAP)

### Approach 2: emlx File Reading (Attempted fix)

```python
# For each message, read emlx file and extract bits 8-10
if server_color is not None:
    actual_color = server_color + 1
else:
    emlx_color = get_emlx_flag_color(file_path)  # bits 8-10
    actual_color = emlx_color + 1 if emlx_color else 1
```

**Pros:**
- Shows SOME color variation for Exchange accounts
- Catches messages that were flagged with a color and never changed

**Cons:**
- emlx colors are STALE - don't update when user changes flag
- User reported: eBay message shows Blue but is actually Orange
- Slower (reads files for each message)
- Can show wrong colors, confusing users

**Results:**
- Red: 6 messages
- Blue: 65 messages (many WRONG - showing original color, not current)

---

## The Specific User Report

**Message:** "eBay Mac Studio order confirmation"
**Actual flag:** Orange ("Waiting")
**What emlx shows:** Blue (4)
**What server_messages shows:** `1` (Orange) for IMAP copy, `NULL` for Exchange copy

The user changed this message's flag from Blue to Orange. The change:
- ✅ Is visible in Mail.app UI
- ✅ Is synced to server_messages (for IMAP copy)
- ❌ Is NOT written back to the emlx file
- ❌ Is NOT written to messages.flags

---

## Technical Details: Flag Bit Encoding

**CORRECTED:** The flag color is stored in **bits 39-41** of the `flags` field, not bits 8-10.

The `flags` field in emlx/messages uses this bit layout:

```
Bits 0-7:   Various message state flags
Bits 8-11:  Unknown (often 12/0xC)
Bits 12-17: Unknown
Bits 39-41: Flag COLOR (0-6)  ← CORRECT LOCATION
```

Color values (bits 39-41):
```
0 = Red
1 = Orange
2 = Yellow
3 = Green
4 = Blue
5 = Purple
6 = Gray
```

**Decoding formula:** `(flags >> 39) & 0x7`

This encoding works for ALL account types (IMAP, Exchange/EWS, etc.) and is stored in:
- `messages.flags` column in the Envelope Index database
- `flags` field in emlx file plist metadata

---

## Questions for Consultation

1. **Is there another data source?** Could Apple Mail store current Exchange flag colors somewhere else (plist files, cache directories, etc.)?

2. **Real-time sync option?** Could we trigger Mail.app via AppleScript to refresh/sync flag data before querying?

3. **Best UX for mixed accounts?** Should we:
   - a) Show accurate IMAP colors + generic 🚩 for Exchange?
   - b) Show emlx colors for all (knowing they may be stale)?
   - c) Try to detect "unchanged" flags where emlx is likely accurate?

4. **Exchange flag sync:** Does Exchange Web Services even support flag colors? Or does Apple Mail only store them locally?

5. **Force refresh?** Is there a way to force Apple Mail to re-write emlx files with current flag data?

---

## File Locations Reference

- **Database:** `~/Library/Mail/V10/MailData/Envelope Index`
- **emlx files:** `~/Library/Mail/V10/{account-uuid}/{folder}.mbox/.../Messages/{rowid}.emlx`
- **Mail preferences:** `~/Library/Containers/com.apple.mail/Data/Library/Preferences/com.apple.mail.plist`

---

## Current State

The codebase currently has Approach 2 (emlx reading) partially implemented but showing wrong colors. We need to decide on the best approach before finalizing.
