---
name: update-my-bot
description: Updates the creator's comment-to-DM bot to the latest version. Use when they say "update my bot", "update me" or "get the latest version". Works out whether the bot runs on their Mac or on their cloud server, runs the safe update script there, and explains what's new, or why it stopped, in plain words.
---

# Updating the bot

The creator wants the newest version of their bot. `scripts/update.sh` does the careful part. It
stops the bot, backs up their settings file (`config.toml`) and their database, gets the new
version from GitHub, upgrades the settings, runs the tests and `igdm check`, and starts the bot
again. If any of that fails, it puts everything back as it was. Your job is to run it in the right
place, read what it says, and tell them in plain words.

Talk to them in the voice from [../setup/reference/voice.md](../setup/reference/voice.md). Plain,
friendly English in short sentences, with no dashes as punctuation, no "Label: text" sentences and
no clipped fragments. One step at a time.

## 1. Work out where the bot runs

Run these from the project folder.

- If `data/MOVED_TO_SERVER` exists, the bot was moved to their server (chapter 13 of the setup).
- If `.setup-progress.json` has `"platform": "windows"`, the bot always runs on the server.
- Otherwise it runs on this Mac.

Before you start, tell them in a sentence or two what's about to happen:

> I'll update your bot now. It pauses for a minute or two, so nobody gets half a message, and
> your settings, keys and records stay exactly as they are. If anything goes wrong, it puts your
> bot back the way it was.

**On the server.** Run

```
ssh igdm 'cd app && bash scripts/update.sh'
```

It can take a few minutes, because it runs the tests. Run it with the Bash tool's timeout set to
600000 ms, or in the background and watch it to the end. Never interrupt it, because a cut-off
update has to put everything back.

When it has finished with exit code 0, also
bring the copy on their computer up to date, because that's where the setup skill and the server
scripts live. Run `git pull --ff-only` in the local folder. If that refuses, it says which files
are in the way. Show them, and ask before changing anything (see section 3).

If the update upgraded the settings on the server, the server's `config.toml` now differs from
the copy on their computer. Look in the server's update log (its path is in the script's last
line, and the log is that folder's `update.log`) for `settings upgraded from version`. If it's
there, run `bash scripts/pull_config.sh` so their local copy matches, before anyone edits it.
On Windows, ask first whether they have settings changes they haven't sent yet, because the pull
replaces the local copy.

**On this Mac.** Run `bash scripts/update.sh`. Run it with the Bash tool's timeout set to
600000 ms, or in the background and watch it to the end. Never interrupt it, because a cut-off
update has to put everything back.

**On Windows in the middle of the setup.** The update runs `igdm check`, which fails until the
Instagram connection is set up, so an update before the setup's chapter 11 ends with exit code 2
and puts the old version back. If they're still setting up, suggest updating once the setup is
done. If it already ran, explain that this is why, and that nothing was lost.

## 2. Read the exit code

Note the exit code, and read every line it printed.

### 0 means it worked

- `Already up to date.` means there's nothing new. Tell them their bot already has the newest
  version.
- `Updated to <version>. What's new:` is followed by one line per change. Turn those lines into a
  few plain sentences about what's better for them. Skip the technical ones, or sum them up as
  "and some smaller fixes". The last line, `Your old settings and database are saved in
  <folder>` (just `settings` if there's no database yet), says where the backup is. Tell them
  it's there in case they ever need it.

Then check the bot is running again. On a Mac, `tail -n 20 logs/igdm.log` should show a new `tick`
line within a minute or two. On the server, use `ssh igdm 'tail -n 20 app/logs/igdm.log'`. On
Windows before the setup's chapter 12, the bot isn't started yet, so there's no `tick` line, and
that's fine.

### 1 means it refused, and nothing changed

Explain the refusal in plain words, and offer the fix. These are the messages it can print.

- **`You've changed these files, so I won't update over them: <files>`** Someone edited files that
  came with the bot. Show them which ones with `git status --short` and what changed with
  `git diff` (on the server, run both inside `ssh igdm 'cd app && …'`). Explain each change in a
  sentence. Then ask what they'd like to do. Never discard anything without a clear yes.
  - If they want to keep a change, it can't stay in that file through an update. Offer to save a
    copy first, for example into `data/backups/my-changes-<date>/`, so nothing is lost.
  - Only when they've said yes, put those files back with `git checkout -- <the files>`, and run
    the update again.
- **`This copy has its own changes saved in git that aren't on GitHub, so I won't update over
  them.`**
  Show them the extra versions with `git log --oneline origin/main..HEAD`, and explain what they
  are. Ask before doing anything. If they want them gone, offer to keep them on a side branch first
  (`git branch my-changes-<date>`). Only after a clear yes, move the folder to GitHub's version with
  `git reset --hard origin/main`, and run the update again.
- **`I couldn't reach GitHub to check for a new version.`** Check their internet, and ask them to
  turn off any VPN. On the server, also check `ssh igdm true` works, which needs Tailscale on.
  Then run it again.
- **`This Mac handed the bot to your server, so there's nothing to update here.`** The bot runs on
  the server, so update it there, with the server command above.
- **`On Windows your bot runs on your server, not on this computer, so there's nothing to update
  here.`** Someone ran `scripts/update.sh` on the Windows computer itself. On Windows the bot
  always runs on the server, so update it there, with the server command above.
- **`I can't find your settings file (config.toml)`** The settings file is missing. On the server,
  it hasn't been sent yet, so run `bash scripts/push_config.sh` first. On a Mac, follow
  [../setup/reference/stuck.md](../setup/reference/stuck.md).
- **`Updates need the project downloaded with git.`** The folder was downloaded without git, for
  example as a ZIP file. Chapter 1 of the setup moves them to a git download. Do that step with
  them, and ask before moving anything.
- **`uv isn't installed`** or **`sqlite3 isn't installed`** A tool the update needs is missing. On
  a Mac, chapter 1 of the setup installs uv. Ask before installing anything. On the server, the
  setup installed both, so follow [../setup/reference/stuck.md](../setup/reference/stuck.md).
- **`I couldn't find the main version on GitHub`**, **`I couldn't make a backup folder`**,
  **`I couldn't stop your bot`** or **`I couldn't back up your …`** Nothing was changed. Follow
  [../setup/reference/stuck.md](../setup/reference/stuck.md), and read the update log at the path
  it printed after `Ask Claude to look at:`. If it says the bot may not be running, check that
  first, as in the exit code 0 part above.

### 2 means the update failed, and it put the bot back

Read the last lines carefully, because they say how far it got.

- `The update didn't work, so I put your bot back as it was.` means their bot is back on the old
  version and running as before. Say so first, so they don't worry:

  > The update didn't go through, so I put your bot back exactly as it was. It's running as
  > before, and nothing was lost. Let me look at why.

- If it also says `But your bot didn't start again, so it isn't running now.`, say that honestly
  and start it first. On a Mac that's `bash scripts/install_agent.sh`. On the server it's
  `ssh igdm 'sudo systemctl enable --now igdm igdm-dashboard'`, which starts the bot and the
  dashboard together, then check with `ssh igdm 'systemctl is-active igdm igdm-dashboard'`. On
  Windows before the setup's chapter 12, neither was running before the update, so leave them
  off.
- If it says `I couldn't put everything back by myself.`, tell them their settings and database
  are safe in the folder it named, and that you'll sort it out with them now.

Then follow [../setup/reference/stuck.md](../setup/reference/stuck.md). Start with the log path the
script printed after `Ask Claude to look at:`. On a Mac, read it with `tail -n 80 <that path>`. On
the server, read it with `ssh igdm 'tail -n 80 <that path>'`. It holds the output of each step,
like the tests and `igdm check`, so the reason is usually near the end. Explain what you found, and
the fix, before trying the update again.

### Any other exit code means the server couldn't be reached

An exit code like 255 comes from `ssh`, not from the update, so the update didn't run and nothing
changed. Ask them to check the Tailscale app on their computer says connected, and to turn off any
other VPN. Then run `ssh igdm true`. When that works, run the update again. If it still fails,
follow [../setup/reference/stuck.md](../setup/reference/stuck.md).

## 3. Never throw away their changes without asking

Never run `git reset`, `git checkout --` or `git clean` on their folder, on their computer or on
the server, without asking first and getting a clear yes. Before you ask, show them exactly which
files it would change and what would be lost. Offer to save a copy first. Their `config.toml`,
`.env`, `data/` and `logs/` belong to them, so never touch them as part of an update, and never
open `.env`.
