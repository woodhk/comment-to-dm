# Using Chrome

You open the web pages and find the buttons with Claude in Chrome, a browser extension that lets
you see and use tabs in their own Chrome. They stay logged in as themselves, and you never type a
password or click a button that's theirs (see [who-does-what.md](who-does-what.md)).

## Connect the extension (chapter 1)

1. Check whether the browser tools work. Try to list their tabs. If that works, Chrome is
   connected, and you can skip to the end of this section.
2. If not, check Chrome itself is installed, because a page you open may otherwise land in
   another browser (on Windows that's usually Edge), and the extension would go there.
   - **Mac.** `ls -d "/Applications/Google Chrome.app" "$HOME/Applications/Google Chrome.app" 2>/dev/null`
   - **Windows.** `ls "/c/Program Files/Google/Chrome/Application/chrome.exe" "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" "$(cygpath "$LOCALAPPDATA")/Google/Chrome/Application/chrome.exe" 2>/dev/null`

   If it prints nothing, Chrome isn't there. Tell them Chrome is free, and open
   https://www.google.com/chrome/ for them (with `open "<url>"` on a Mac, or
   `cmd.exe //c start "" "<url>"` on Windows, since Chrome isn't there yet). The download and the
   install are theirs. When it's installed, run the check again.
3. Tell them what the extension is for in one or two sentences, then help them install it. The
   install page is in the Chrome Web Store:
   https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn
   Open it in Chrome itself, as in "Opening a page in Chrome" below. They click "Add to Chrome",
   then sign in to the extension with the same Claude account they use for Claude Code.
4. Ask them to type `/chrome` in Claude Code and press Enter. A panel shows the connection. It's
   working when it says "Status: Enabled" and "Extension: Installed". If it offers
   "Enabled by default", suggest they pick it, so it connects every time.
5. Try listing the tabs again.

If it still won't connect, go through these, one at a time, checking after each:

1. In `/chrome`, choose "Reconnect extension".
2. Quit Chrome fully and open it again. The first connection sets up a small file that Chrome only
   reads when it starts.
3. Quit Claude Code and start it again with `claude --chrome`, from the project folder.
4. Open `chrome://extensions` and check the Claude extension is switched on. If it is, remove it
   and install it again from the link above.
5. Check the extension is signed in to the same Claude account and organisation as Claude Code.
   Typing `/status` in Claude Code shows which one Claude Code uses.

It can take a few tries. That's normal, so keep it light.

## Opening a page in Chrome

When the extension is connected, open pages with its own tab tools. Otherwise, open a page in
Chrome with a command.

- **Mac.** `open -a "Google Chrome" "<url>"`
- **Windows.** `cmd.exe //c start "" chrome "<url>"`

On Windows, cmd.exe cuts a link at the first `&`, so a link with `&` in it opens cut short. For
those, use `powershell.exe -NoProfile -Command "Start-Process chrome '<url>'"` instead. If a page
still doesn't open, or opens in the wrong browser, tell them the address in plain text and ask
them to paste it into Chrome's address bar.

## If it won't connect at all

Carry on without it. Nothing in the setup needs the extension. It just saves them clicks.

- Open each page in Chrome with the commands in "Opening a page in Chrome" above. If Chrome
  isn't there, their normal browser is fine, with `open "<url>"` (Mac) or
  `cmd.exe //c start "" "<url>"` (Windows). A link with `&` in it needs the PowerShell form above.
- Describe the clicks in words. Name the button exactly as it's written, say roughly where it is
  on the page, and say what they'll see after.
- Ask them to tell you what they see, since you can't see it yourself. If they're unsure, they can
  take a screenshot and drag it into the chat. Remind them to make sure no secret is showing in it.

## Before each new site

The extension asks permission the first time you use each website. Warn them before it pops up,
so it doesn't look like something went wrong:

> In a moment Chrome will ask whether I can use this website. That's the extension being careful.
> Please click Allow, and then I can carry on.

The sites in this setup include instagram.com, facebook.com, business.facebook.com,
developers.facebook.com, myaccount.google.com, platform.openai.com, and for the cloud chapter
hetzner.com, tailscale.com, login.tailscale.com, healthchecks.io and their dashboard's own
address (it ends in `.ts.net`). Until a site is allowed, actions on it fail with
"Permission denied". If that happens, ask them to allow it and try again.

## Reading a page

- Read the page text first.
- Some pages, like Meta's app dashboard, return little or no text. Take a screenshot instead and
  work from what you see.
- Never click Show, Copy, Reveal or Generate on a secret. Those clicks are always theirs.

## Hands off while a secret may be on screen

A page read, a find or a screenshot captures whatever is on the page, secrets included. So from
the moment you ask them to reveal or make a secret, stop looking at that tab. This covers the Meta
App secret's Show button, the token in Graph API Explorer, OpenAI's new key dialog, Google's app
password, and the healthchecks.io ping URL.

1. Tell them where the secret is and what to click, then open the ask_key window
   (see [who-does-what.md](who-does-what.md)).
2. Until they say they've pasted it into that window, closed the dialog or left the page, make no
   page reads, finds or screenshots of that tab. Listing tab titles is fine.
3. When they say so, check the tab title first. Only read the page again once the secret can't
   be showing any more.

If you capture a secret by mistake, don't repeat it or use it. Tell them, and help them make a new
one.

## Filling in fields

- Only fill in fields that aren't secret, like an app name or a server name.
- Set a field's value directly rather than typing into it, key by key. Typing can break long
  values. For example, Hetzner's SSH key box said "SSH key has invalid format" when the key was
  typed, and accepted it when the value was set directly. An SSH public key is safe to fill in,
  because it's the public half.
- Never fill in a password, a card number or a 2FA code. Those are always theirs.

## Things that need them

When a login page, a 2FA prompt, a CAPTCHA (a "prove you're human" puzzle) or a dialog that grants
access appears, stop and hand over. Say what they'll see and what to choose, then wait.
