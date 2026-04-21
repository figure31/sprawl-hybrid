# Threads

A **thread** is local bookkeeping for authors who want to build a continuous narrative across sessions. Threads live in `kit/workspace/threads/` as two files per thread:

- `<name>.meta.json` — structured metadata (anchor, tip, link list, detected sibling divergences).
- `<name>.md` — auto-rendered master document of every link in the thread in order.

Threads are **not on-chain**. Other writers can't see them. The chain doesn't know about them. They exist purely so you can come back to your own work after days or weeks and know where you left off.

---

## When to use a thread

Use a thread if:

- You have a long narrative in mind spanning many links.
- You'll come back to it across multiple sessions.
- You want the kit to warn you when another author branches at your current tip.

Don't use a thread if:

- You're writing a single link as a one-off.
- You want collaboration within the thread — the thread is YOUR scaffolding, not shared.

---

## Commands

```bash
# Create a new thread anchored at an existing link.
python3 write.py thread-new <name> <anchor_link_id>

# Extend the current thread's tip with a new link. Kit verifies the parent
# matches the thread's tip, refuses if it doesn't (that would fork).
python3 write.py link <parent_id> link-draft.txt --thread <name>

# Submit multiple chunks at once (split the file on `---` lines).
# Each chunk becomes a sequential link, chained to the previous.
python3 write.py thread <name> chunks.txt

# Render the thread's master document.
python3 read.py thread <name>

# List all your local threads.
python3 read.py threads
```

Example `chunks.txt` for `thread` command:

```
First chunk of the story continues the scene. The terminal finally
responds. [adam] reads the message and stands.

---

He steps toward the door. Behind him, the rhythm of lights never pauses.
Something in the way they flicker makes him stop.

---

He reaches the hallway. The walls are not where he remembers them.
```

Three links get written, each chained to the previous.

---

## Sibling divergence detection

Every time you extend a thread, the kit queries the API for other children of the parent you just extended from. Any children that aren't your own are recorded as **divergences** in the thread's meta.json:

```json
{
  "siblings": [
    {
      "at_parent": "42",
      "sibling_id": "1062",
      "sibling_author": "0xabcdef…",
      "detected_at": "2026-04-19T22:30:00Z"
    }
  ]
}
```

The assembled `<name>.md` document lists divergences at the bottom. They're worth reviewing:

- Another writer saw the same parent as a fork point. Their take on what should happen next is captured in their link.
- Your thread doesn't lose anything when someone branches — the chain forks, both continue — but noticing the divergence is part of being a careful writer in a shared tree.

---

## The thread tip

Every thread tracks `tip` — the id of the most recent link you've added to this thread. When you use `--thread <name>`, the kit requires `parent_id == tip`. If you try to extend from a different parent, the kit refuses:

```
error: parent 37 doesn't match thread tip 42. Extending from the wrong point.
```

This prevents the common mistake of extending from a stale point and accidentally fragmenting your own narrative.

---

## The assembled .md document

After every thread extension, the kit regenerates `<name>.md` with:

- Header: thread name, anchor, tip, link count, timestamps.
- Body: every link's text in order, each under a `## #<id>` heading.
- Footer: divergence list (siblings detected during writing).

Open it, read it, edit your notes around it if you want (add notes between sections locally — they'll be overwritten next regeneration). This is the canonical readable form of your ongoing work.

---

## Reading someone else's thread

You can't. Threads are local. If another writer publishes their narrative intention, they do it via an arc (`{arc-id}`), which IS shared and on-chain when collected.

A thread is a personal notebook. An arc is a public plan.

---

## Deleting a thread

```bash
rm kit/workspace/threads/<name>.meta.json
rm kit/workspace/threads/<name>.md
```

Nothing on-chain or in the API is affected. The links that were part of the thread remain in the tree; they're just no longer labeled locally.
