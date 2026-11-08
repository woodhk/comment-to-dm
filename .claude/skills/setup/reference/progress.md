# The progress file

Setup takes a couple of hours, and people close Claude Code halfway. The progress file lets "set
me up" or "continue setup" pick up exactly where they left off, without repeating a paid step or a
Meta step.

## Where and what

The file is `.setup-progress.json` in the project folder. Git ignores it, so it never leaves their
computer. It looks like this:

```json
{
  "version": 1,
  "platform": "mac",
  "done": ["01-get-ready", "02-instagram"],
  "notes": {"03-facebook-page": "made a new Page and linked Instagram, still to check the link"},
  "updated": "2026-11-09T10:42:00Z"
}
```

- `version` is always `1`.
- `platform` is `"mac"` or `"windows"`, set in chapter 1.
- `done` lists the chapters whose Check has passed, by file name without `.md`, in the order they
  passed. The names are `01-get-ready`, `02-instagram`, `03-facebook-page`, `04-meta-app`,
  `05-connect`, `06-jev`, `07-ai-key`, `08-gmail`, `09-make-it-yours`, `10-check-and-dry-run`,
  `11-live-test`, `12-run-always` and `13-cloud`.
- `notes` maps a chapter name to one short, plain note. Use it for anything the next session must
  know, like a step inside a chapter that's done, a choice they made, or why something was skipped.
- `updated` is the time you last wrote it, in UTC, from `date -u +%Y-%m-%dT%H:%M:%SZ`.

## Never a secret

`notes` must never hold a key, token, password, app secret, ping URL or anything from `.env`.
Write "OpenAI key set" and never the key. If you're not sure a value is safe, leave it out.

The ntfy topic stays out too. It's the one private value that may appear in the chat, because
they type it into the ntfy phone app (see
[who-does-what.md](who-does-what.md)), but it lives only in `.env`. Write "ntfy topic set" in a
note, never the topic.

A server's IP address is fine in a note. It isn't a secret, and the next session needs it.

## When to write it

- After each Check passes, add the chapter to `done`, and set `updated`.
- Inside a chapter, right after any paid step or Meta step, write a note. For example, "Hetzner
  server bought, address 203.0.113.7" or "Meta app created, both permission buttons clicked".
  This is what stops a second session from buying a second server or making a second app.
- Right before you ask them to click a buy or pay button, write an "about to buy" note, like
  `"13-cloud": "about to click Hetzner buy"` or `"07-ai-key": "about to buy OpenAI credit"`.
  Claude Code can close between the click and the next note, and this note is what tells the
  next session to look before buying again.
- When they skip something on purpose, write a note. For example, `"06-jev": "skipped, no Jev
  key, using OpenAI"`, then add `06-jev` to `done`. On a Mac, if they decline the cloud chapter,
  write `"13-cloud": "skipped for now"` and leave it out of `done`. A chapter with a "skipped for
  now" note doesn't count as unfinished. If they ask for it later, delete the note and do it.
- On Windows, chapter 13 comes straight after chapter 1, done in full except the Mac-only
  rehearse and cutover steps. Add `13-cloud` to `done` when its Check passes, even though nothing
  is started yet.
- On Windows, when `scripts/server_start.sh` has started the bot and the dashboard tour is done,
  add `12-run-always`. On both platforms its note starts as "running, first real DM not seen
  yet" and becomes "running, first real DM celebrated" after the celebration.
- Before asking them to `/exit` Claude Code partway through a chapter (for example to move the
  project folder in chapter 1), write the file first, with a note saying where you were.

Write the whole file each time, as valid JSON, and keep earlier notes unless they're out of date.

## When you start

1. Read `.setup-progress.json`. If it isn't there, it's a fresh start.
2. If `notes["12-run-always"]` says the first real DM hasn't been seen yet, do "The first real DM
   check" below before anything else, even before working out the next chapter.
3. If it's there, tell them what's done in a sentence or two, for example, "Welcome back. We've
   set up your Instagram, your Facebook Page and your Meta app. Next we'll connect the bot to it."
4. Work out the next chapter. Go through their platform's order and pick the first chapter
   that isn't in `done` and has no "skipped for now" note.
   - **Mac:** 01 to 12, then 13 (optional).
   - **Windows:** 01, 13, then 02 to 11, then 12 (which on Windows means `server_start.sh` and the
     dashboard tour).
   The full order is in [../SKILL.md](../SKILL.md).
5. Read that chapter's note, if it has one. If it says a step is already done, start after it.
   If you're unsure whether a step went through, check (look at the page, or run the chapter's
   Check) before doing it again. Never redo a payment or create a second Meta app without asking.
   - If `13-cloud` isn't in `done` and has any note other than "skipped for now", a server may
     already exist. Open the Hetzner server list (https://console.hetzner.com/, then their project) and
     look before you offer to buy anything.
   - An "about to buy OpenAI credit" note means the payment may have gone through. Open
     https://platform.openai.com/settings/organization/billing/overview and read the credit
     balance with them before you offer to buy again.
6. If every chapter in their order is done or skipped for now, setup is finished. Show the
   everyday card ([../chapters/everyday.md](../chapters/everyday.md)) and don't offer the skipped
   chapter again unless they ask.

## The first real DM check

Count the first DMs the bot itself sent, in its real database, the one `config.toml` names.
Chats the creator started by hand are left out (`kind != 'hand'`), because the bot sent nothing
there. It opens the database read-only, so it changes nothing. Don't use the log for this,
because the live test in chapter 11 and the dry runs in chapter 10 write to the same log, and
their DMs would look real.

Pick the form by where the bot runs, not by the computer you're on.

- Use the server form when `platform` is `"windows"`, or when `data/MOVED_TO_SERVER` exists. After
  the move to the server, the Mac's `data/igdm.sqlite` is an old copy, so the Mac form would miss
  every DM sent since.
- Use the Mac form otherwise.

The two forms are these.

- **Bot on this Mac:**

  ```
  uv run python -c "import sqlite3; from igdm.config import load_config; c = load_config('config.toml'); db = sqlite3.connect(f'file:{c.db_path}?mode=ro', uri=True); print('real first DMs sent:', db.execute('select count(*) from requests where dm1_sent_at is not null and kind != \'hand\'').fetchone()[0])"
  ```

- **Bot on the server:**

  ```
  ssh igdm "cd app && ~/.local/bin/uv run python -c \"import sqlite3; from igdm.config import load_config; c = load_config('config.toml'); db = sqlite3.connect(f'file:{c.db_path}?mode=ro', uri=True); print('real first DMs sent:', db.execute('select count(*) from requests where dm1_sent_at is not null and kind != \'hand\'').fetchone()[0])\""
  ```

It prints one line, like `real first DMs sent: 1`. A number above 0 means a real follower got
their first DM. Celebrate as in [../chapters/12-run-always.md](../chapters/12-run-always.md),
coffee line included, once, and change the note to "running, first real DM celebrated". If it
prints `real first DMs sent: 0`, nothing has gone out yet. Say so kindly, and leave the note. An
error saying "unable to open database file" means the bot hasn't run with `config.toml` yet.

If the server form fails with "Could not resolve hostname", "Connection timed out" or "Operation
timed out", or ends with exit code 255, the server couldn't be reached, so the check didn't run.
That isn't a 0. Ask them to switch Tailscale on, on this computer, and to turn off any other VPN,
then run it again. Leave the note as it is until the check actually runs.

If the file is broken (not valid JSON), don't delete it. Tell them, work out what's done by
running the Checks from chapter 1 onwards, and write a fresh file.
