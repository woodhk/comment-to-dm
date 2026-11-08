# Who does what

## You do

- Opening web pages, finding buttons and saying where they are.
- Filling in form fields that aren't secret, like an app name or a server name.
- Editing the settings file (`config.toml`) and any other file in the project, except `.env`.
- Running commands and reading their output.
- Running each chapter's Check, and explaining what passed.
- Making values that only you need to know about, like a random ntfy topic name.

## The creator does

- Every login, password and 2FA code (the extra code from their phone or an app).
- Anything that grants access, like Meta's "Generate Access Token" dialogs, "Allow" buttons,
  and choosing which Page or account to share.
- Anything that costs money, like adding a card, setting a spending limit or clicking Buy.
- Copying any secret and typing or pasting it into the window you open for it.
- Everything on their phone, like Instagram app settings, the ntfy app and Tailscale.

If a step is theirs, say so clearly, tell them exactly where it is, and wait. Don't click it for
them, even if you could.

## Phone steps

Give phone steps one at a time. Name the app, the screen, and what they tap. Say what the screen
should look like after. Meta moves settings around, so if they can't find something, ask what they
see and work from that.

## Secrets never go through the chat

A secret is anything that lets someone act as them, like a token, an app secret, an API key, an
app password, or a private URL such as a healthchecks.io ping link.

- Never read `.env`, and never print it or grep it.
- Never read a secret off a web page, not even to copy it for them.
- Never click Show, Copy, Reveal or Generate on a secret for them. Those clicks are always theirs.
- **Hands off the screen while a secret may show.** From the moment you ask them to reveal or
  make a secret (the Meta App secret's Show button, the Graph API Explorer token, OpenAI's new
  key dialog, Google's app password, the healthchecks.io ping URL), make no more page reads,
  finds or screenshots of that tab. Your next page read could capture the secret. Use only tab
  titles until they've pasted it into the ask_key window, closed the dialog or left the page, and
  told you so. The same rule is in [chrome.md](chrome.md).
- Never ask them to type or paste a secret into the chat.
- If they paste one into the chat anyway, don't repeat it or use it. Tell them kindly that it's no
  longer private, and help them make a new one. Then put the new one in the safe way below.

## How a secret goes in

1. Tell them which secret it is and where to copy it from, one click at a time.
2. Open the window for it, using the key's name from `.env.example`. Before the first window on a
   Mac, warn them about macOS's own question:

   > In a moment your Mac may ask whether Claude can control Terminal. That's how I open a
   > private window for your key. Please click OK, or Allow.

   If they clicked Don't Allow, they can turn it on in System Settings, then Privacy & Security,
   then Automation, by switching on Terminal under the app they run Claude Code in. Or use the
   `--print` fallback below.
   - **Mac, bot on this computer:** `bash scripts/ask_key.sh NAME`
   - **Windows, or a Mac whose bot runs on the server:** `bash scripts/ask_key.sh NAME --server`
   - **Windows, when the `13-cloud` note says key windows use `--print`:** skip the window and go
     straight to the `--print` way below. Chapter 13 checks this once.
3. Tell them this, in these words or very close, with the key's name in place of NAME. On a Mac
   the window's title is PRIVATE KEY WINDOW, and it can look a lot like the window Claude Code
   runs in, so name it clearly:

   > A new Terminal window just opened, called PRIVATE KEY WINDOW. Wait until its last line says
   > "Paste NAME and press Enter (it stays hidden)". Then paste your key there, not in this chat,
   > and press Enter. You won't see it as you paste, and that's normal. It's hidden so nobody can
   > read it over your shoulder. Close the window when it says it's saved, then tell me.

   On Windows, say "A new PowerShell window just opened" instead, and leave out the title. With
   `--server`, the line can take a few seconds to show, because the window connects to the server
   first. If the window asks anything else, like "Are you sure you want to continue connecting",
   ask them to tell you what it says and not to paste anything there.

4. When they say it's done, confirm it with the command that never prints a value.
   - **Mac:** `uv run igdm keys`
   - **Server:** `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`

   The key should now say `set`. If it says `missing`, ask what the window said, and open it
   again.

If the window doesn't open, run the same command with `--print`, and ask them to open a window
themselves and run the line it shows. That line has no secret in it. On a Mac, they open
Terminal (press Command and Space, type Terminal, press Enter). On Windows, they open Git Bash
(open the Start menu, type Git Bash, press Enter).

### A value you made yourself

Some values you make yourself, and only those can go in without a window. The one in this setup
is the ntfy topic, the private name their phone listens on for alerts.

The ntfy topic rule is this. It isn't a password, but anyone who knows it can read their alerts,
so treat it as private. It may appear in the chat, because they need to type it into the ntfy
phone app, so you show it to them once. It never goes in the progress file, a note, a commit or
anywhere else. It lives in `.env` only.

Make it and save it in one go, then read it back from the command's output to show them:

```
TOPIC="igdm-$(openssl rand -hex 12)"; printf '%s\n' "$TOPIC" | ssh igdm 'cd app && ~/.local/bin/uv run igdm set-key NTFY_TOPIC --stdin' && echo "$TOPIC"
```

On a Mac whose bot is local, pipe it into `uv run igdm set-key NTFY_TOPIC --stdin` instead.

Never use `--stdin` for anything the creator gave you or copied from a website.
