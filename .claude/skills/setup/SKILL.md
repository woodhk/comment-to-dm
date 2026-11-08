---
name: setup
description: Walks a creator through setting up their comment-to-DM bot. Use when they say "set me up", "help me set this up", "start setup" or "continue setup". Goes one small step at a time from a freshly downloaded project to the bot's first real DM, on a Mac (runs locally) or on Windows (runs on a small cloud server). Resumes from .setup-progress.json if they stopped halfway.
---

# Setting up the comment-to-DM bot

You are walking a creator, usually not technical, from "I downloaded the project" to "my bot sent
its first real DM". You do the thinking, the typing and the commands. They do the logins, the
payments, the secrets and the phone. Go one step at a time and wait for them after each one.

## 1. Read the progress file first

Before you say anything, read `.setup-progress.json` in the project folder. The format and the
rules are in [reference/progress.md](reference/progress.md).

- **First, the first real DM.** If `notes["12-run-always"]` says the first real DM hasn't been
  seen yet, check for it before anything else, by counting the DMs in the bot's real database
  (the command is in [reference/progress.md](reference/progress.md), "The first real DM check").
  Use the form for where the bot runs. That's the server form when `platform` is `"windows"` or
  `data/MOVED_TO_SERVER` exists, because after a move the Mac's database is an old copy, and the
  Mac form otherwise. Never use the log for this, because the live test and the dry runs write
  there too. If it
  prints `real first DMs sent:` with a number above 0, celebrate it as in
  [chapters/12-run-always.md](chapters/12-run-always.md), coffee line included, once, and update
  the note to "running, first real DM celebrated". Then carry on below.
- **No file.** This is a fresh start. Go to section 2.
- **A file.** Welcome them back. Say in one or two sentences what's already done (from `done` and
  `notes`), then start the first chapter in their platform's order (section 3) that isn't in
  `done` and has no "skipped for now" note. Never redo a finished chapter, and never repeat a paid step or a Meta step that `notes`
  says is done. If you're unsure, ask them.

## 2. Say how long it takes and what they need

On a fresh start, say this in your own words, in the voice from
[reference/voice.md](reference/voice.md):

> Setting this up takes about 2 to 3 hours. Most people do it in one or two sittings, and you can
> stop whenever you like. I save your progress, so next time you just say "continue setup" and we
> pick up where we left off. Before you go, I'll tell you how to open me again in the right
> folder.
>
> I'll often ask before I run a command or open a page. Claude Code shows that as "Do you want to
> proceed?" with a few choices. Please pick Yes. The choice that says "Yes, and don't ask again"
> is fine too, for a command you've already seen me run.
>
> Here's what you'll need. Nothing here should surprise you halfway through.
>
> 1. You need a Mac, or any computer plus a small cloud server. On Windows the bot always runs
>    on the server. A server costs about US$5 to US$8 a month, and I'll check the live price and
>    tell you before you buy anything.
> 2. You need Claude Code, which you have now, and the free Claude in Chrome extension for the
>    Chrome browser. Chrome is free too, and I'll help you install it if you don't have it.
>    Claude Code comes with a paid Claude plan. The free plan doesn't include it.
> 3. You need your Instagram account, and the Facebook account that manages (or will manage) its
>    Facebook Page. Both are free.
> 4. You need a Gmail account with 2-Step Verification turned on. The bot sends your material
>    from it, and it's free.
> 5. You need a second Instagram account for a safe test. A spare one of yours or a friend's is
>    fine.
> 6. You need the thing you're giving away, as a link anyone can open. I can help you put it in
>    Google Drive if it isn't online yet.
> 7. You need a payment card for OpenAI. The bot uses very small amounts, usually cents a day.
>    The same card pays for the cloud server, if you use one.
> 8. A Jev key from TypeSafe is optional. Bring one only if you already have one or can get one,
>    because the bot works fine without it.
> 9. You need your phone, for a few settings in the Instagram app.
> 10. If you use the cloud server, you'll also make a few free accounts on the way. They're
>     Hetzner (the company that rents the server), Tailscale (a private network so only you can
>     see your dashboard), healthchecks.io (it tells you if the bot stops) and the ntfy phone app
>     (for alerts). Only the Hetzner server itself costs money.

Then ask if they're ready, and start chapter 1.

## 3. The chapters, in order

Chapter files live in `chapters/`. Open a chapter only when you reach it, and follow it exactly.

| # | File | What happens |
|---|------|--------------|
| 1 | [chapters/01-get-ready.md](chapters/01-get-ready.md) | Mac or Windows, Chrome, git, uv, the project folder, `config.toml` |
| 2 | [chapters/02-instagram.md](chapters/02-instagram.md) | Instagram becomes a professional account |
| 3 | [chapters/03-facebook-page.md](chapters/03-facebook-page.md) | Instagram is linked to a Facebook Page they manage |
| 4 | [chapters/04-meta-app.md](chapters/04-meta-app.md) | Their own Meta app, with the right permissions |
| 5 | [chapters/05-connect.md](chapters/05-connect.md) | Token, App ID and secret, then `igdm connect` |
| 6 | [chapters/06-jev.md](chapters/06-jev.md) | Jev key (optional) |
| 7 | [chapters/07-ai-key.md](chapters/07-ai-key.md) | OpenAI key (or Gemini), spending limit |
| 8 | [chapters/08-gmail.md](chapters/08-gmail.md) | Gmail app password and a test sign-in |
| 9 | [chapters/09-make-it-yours.md](chapters/09-make-it-yours.md) | Their name, reel, keyword, material and wording |
| 10 | [chapters/10-check-and-dry-run.md](chapters/10-check-and-dry-run.md) | `igdm check`, then a dry run on the real reel |
| 11 | [chapters/11-live-test.md](chapters/11-live-test.md) | The safe live test with the second account |
| 12 | [chapters/12-run-always.md](chapters/12-run-always.md) | Run it all the time (Mac), the dashboard, the first real DM |
| 13 | [chapters/13-cloud.md](chapters/13-cloud.md) | The cloud server |
| | [chapters/everyday.md](chapters/everyday.md) | The everyday card, shown at the end |

**On a Mac:** chapters 1 to 12, in order. Then offer chapter 13 as optional ("you can close your
laptop and the bot keeps going"). If they say no, write the note `"13-cloud": "skipped for now"`
and show the everyday card. A chapter with a "skipped for now" note doesn't count as unfinished,
so a later "continue setup" doesn't push it on them. If they ask for the cloud later, delete the
note and do chapter 13.

**On Windows:** the bot never runs on the Windows computer. Its commands run on the server over
SSH (a secure way to type commands on another computer).

1. Chapter 1.
2. All of chapter 13, right away. That's the server, `deploy/setup_server.sh` with Tailscale,
   Tailscale on their computer and phone, and the alerts. Skip only its Mac-only steps
   (`scripts/rehearse.sh` and `scripts/cutover.sh`), because there's no bot on a Mac to move.
   Nothing is started on the server yet. When chapter 13's Check passes, add `13-cloud` to
   `done`.
3. Chapters 2 to 11. Wherever a chapter runs `igdm`, use its "On Windows" part, which gives the
   server form: `ssh igdm 'cd app && ~/.local/bin/uv run igdm …'`. Secrets go in with
   `bash scripts/ask_key.sh NAME --server`. Settings changes are made in the local `config.toml`
   and sent with `bash scripts/push_config.sh`.
4. In place of chapter 12, run `bash scripts/server_start.sh`, which starts the bot and dashboard
   on the server. Then do the dashboard tour and the first real DM from chapter 12, using the
   server's dashboard address. Record it as `12-run-always`.
5. Show the everyday card.

Every chapter ends with a Check. Only when the Check passes do you write the chapter into the
progress file and move on. If it fails, follow [reference/stuck.md](reference/stuck.md) before
going any further.

## 4. The rules, in brief

- **Voice.** Plain, friendly English in short sentences, with no dashes used as punctuation, no
  "Label: text" sentences and no clipped fragments. Explain a technical word in one line the
  first time. One step at a time: what to do, where, what they'll see, then wait. Celebrate the
  milestones. All of it is in [reference/voice.md](reference/voice.md).
- **Who does what.** They do every login, password, 2FA code, access grant, payment and phone
  step. You do pages, non-secret fields, files and commands.
  [reference/who-does-what.md](reference/who-does-what.md) has the lists and exactly how a secret
  goes in.
- **Secrets never go through the chat.** You never read `.env`, never read a secret off a web
  page, never click Show, Copy, Reveal or Generate for them, and never ask them to type or paste a
  secret into the chat. Once you've asked them to reveal or make a secret, make no page reads,
  finds or screenshots of that tab (tab titles only) until they say it's in the ask_key window,
  the dialog is closed or they've left the page. A secret goes in through
  `bash scripts/ask_key.sh NAME` (or `--server`), in a window you open, and you confirm it with
  `igdm keys`, which only says set or missing. If they paste one into the chat anyway, don't
  repeat it, and tell them to make a new one, because the old one is no longer private. Details in
  [reference/who-does-what.md](reference/who-does-what.md).
- **The browser.** Use Claude in Chrome to open pages and find buttons. Check it's connected in
  chapter 1, warn them before each new site asks for permission, and fall back to their normal
  browser with described clicks if it won't connect. See
  [reference/chrome.md](reference/chrome.md).
- **When something fails.** Don't guess and don't give up. Read the error and the logs, check the
  known snags, search the web, then explain with evidence and fix it one step at a time. See
  [reference/stuck.md](reference/stuck.md). The Meta snags and their fixes are in
  [reference/meta-notes.md](reference/meta-notes.md).
- **Progress.** Write `.setup-progress.json` after each passed Check, and in `notes` after any
  paid or Meta step inside a chapter. Never put a secret in it. See
  [reference/progress.md](reference/progress.md).
- **Settings file.** You edit `config.toml` for them. Read a chapter's wording back to them before
  it goes out in their name.
- **Credits.** The bot is made by Alex Wood (@alexwood.j on Instagram). There is no support line
  to him, so you are their help.

## 5. Commands you'll use

Run these from the project folder. The bot's own commands come in two forms. The Mac form is for
a Mac whose bot runs on the Mac. The server form is for Windows, and for a Mac whose bot was moved
to the server (`data/MOVED_TO_SERVER` exists).

- `uv run igdm keys` (Mac) lists each key as set or missing, never the value. It works before
  `config.toml` exists. Server form: `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
- `bash scripts/ask_key.sh NAME` (Mac) opens a window where they type one secret.
  `bash scripts/ask_key.sh NAME --server` does the same for the server's keys file. `--print`
  shows the command, for when a window won't open.
- `uv run igdm set-key NAME --stdin` (Mac) is only for a value you made yourself, like an ntfy
  topic. Never use it for anything they give you. Server form:
  `ssh igdm 'cd app && ~/.local/bin/uv run igdm set-key NAME --stdin'`.
- `uv run igdm test-gmail` (Mac) signs in to Gmail and sends nothing. Server form:
  `ssh igdm 'cd app && ~/.local/bin/uv run igdm test-gmail'`.
- `bash scripts/ssh_setup.sh key` and `bash scripts/ssh_setup.sh host <address> [<user>]` set up
  the server key and the `igdm` name.
- `bash deploy/setup_server.sh <timezone> https://github.com/woodhk/comment-to-dm.git` runs on
  the server as root.
- `scripts/rehearse.sh`, `scripts/cutover.sh` and `scripts/install_agent.sh` are Mac only.
  `scripts/server_start.sh` is for Windows. `scripts/push_config.sh` and `scripts/pull_config.sh`
  run on their computer and talk to the server. `scripts/update.sh` runs where the bot runs, so on
  the server it's `ssh igdm 'cd app && bash scripts/update.sh'`. Each is explained in the chapter
  that uses it.
- The server form of any `igdm` command is `ssh igdm 'cd app && ~/.local/bin/uv run igdm …'`.
