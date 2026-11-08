# Chapter 1: Get ready

## Why this matters

This chapter makes sure their computer has the few tools the bot needs, and that the project
folder is in a good place. Everything later builds on it, so it's worth getting right now.

## Steps

Do these one at a time. Tell them what you're checking in a sentence, run it, and say what you
found. Ask before you install anything.

1. **Mac or Windows.** Run `uname -s`. `Darwin` means a Mac. Anything starting with `MINGW` or
   `MSYS` means Windows. Tell them which path they're on and what it means for them.
   - On a Mac, say the bot will run on their computer, and later they can move it to a small
     cloud server if they like.
   - On Windows, say the bot will live on a small cloud server, and their computer is where they
     do the setup. That's why the cloud chapter comes next on Windows, before the Instagram parts.
   Remember the platform. You write it to the progress file at the end of this chapter.

2. **Chrome.** Check the Claude in Chrome extension is connected, and help them connect it if not.
   Follow [../reference/chrome.md](../reference/chrome.md), which also checks Chrome itself is
   installed (on Windows, a page you open would otherwise go to Edge). If the extension won't
   connect after the retries, tell them that's fine and carry on without it.

3. **git.** git is the tool that downloads the bot and its updates. Run `git --version`.
   - **Mac, if it's missing.** Run `xcode-select --install`. Tell them a small window will pop up
     from Apple asking to install the "command line developer tools". They click Install, then
     Agree. It takes a few minutes. Ask them to tell you when it says it's finished, then run
     `git --version` again.
   - **Windows, if it's missing.** Claude Code on Windows needs Git for Windows, so this is rare.
     Open https://git-scm.com/downloads/win and walk them through the installer with its default
     choices. They need to restart Claude Code afterwards, then say "continue setup".

4. **The project folder.** Check you're in the right folder, and that it was downloaded with git.
   - **Mac.** If the folder is in Downloads, Desktop or Documents, macOS may ask something like
     "Terminal would like to access files in your Downloads folder" (with the name of the app they
     run Claude Code in). Warn them before the first command here, and tell them to click Allow.
   - Run `ls pyproject.toml config.example.toml .env.example`. All three should be there.
   - Run `git remote get-url origin`. It should show `github.com/woodhk/comment-to-dm`.
   - Both fixes below make a new folder at `~/comment-to-dm`. Before either one, run
     `test -e ~/comment-to-dm && echo taken || echo free`. If it says `taken`, stop and ask them
     what that folder is. It may be an earlier download they can use instead. Never overwrite,
     move or delete it.
   - If there's no git (for example they downloaded a ZIP file), explain that updates need git.
     With their OK, run `git clone https://github.com/woodhk/comment-to-dm.git ~/comment-to-dm`.
     There's nothing to keep from the old folder yet.
   - **Mac only.** Run `pwd`. If the folder is inside Desktop, Documents, Downloads or iCloud
     Drive, explain that macOS stops background programs from reading those folders, so the bot
     couldn't run on its own later. With their OK, move it to their home folder with
     `mv "$(pwd)" ~/comment-to-dm`.
   - After either fix, and only once the new folder is there, write a partial progress file in
     it, at `~/comment-to-dm/.setup-progress.json`, with their `platform`, `"done": []` and a
     note on `01-get-ready` saying where you were (for example "moved the folder to
     ~/comment-to-dm, next is uv"). Then they reopen Claude Code in the new folder.
   - How they reopen depends on how they run Claude Code, so ask them if you aren't sure.
     - **In Terminal or Git Bash.** They type `/exit`. Then in Terminal on a Mac (press Command
       and Space, type Terminal, press Enter) or Git Bash on Windows (open the Start menu, type
       Git Bash, press Enter) they run `cd ~/comment-to-dm` and then `claude`, and say
       "continue setup".
     - **In the Claude desktop app's Code tab.** They start a new session, and in its folder
       picker they choose the `comment-to-dm` folder in their home folder. Then they say
       "continue setup".

5. **uv (Mac only).** uv is the tool that installs the right version of Python and everything the
   bot uses, so they don't need to install Python themselves. Run `command -v uv`.
   - If it's missing, ask for their OK, then install it with the command from
     https://docs.astral.sh/uv/ (today that's `curl -LsSf https://astral.sh/uv/install.sh | sh`).
     Read what the installer prints at the end. If it says to add a line to a shell file or to run
     `source` on something, do what it says.
   - In this session, if `uv` still isn't found, use `export PATH="$HOME/.local/bin:$PATH"`
     before each `uv` command, or the path the installer printed.
   - Then check that a brand new Terminal window will find it, because later you open windows for
     their secrets and those run `uv`. Run `"$SHELL" -lic 'command -v uv'`. It should print a
     path. If it prints nothing, add the PATH line the uv installer gave (for zsh, the usual line
     is `. "$HOME/.local/bin/env"` at the end of `~/.zshrc`; for bash, `~/.bash_profile`) and run
     the check again.

6. **The settings file.** The settings file, `config.toml`, holds everything about their bot that
   isn't a secret, like the reel, the keyword and the wording.
   - If `config.toml` doesn't exist yet, run `cp config.example.toml config.toml`. If it already
     exists, leave it as it is.
   - Set its `timezone` line to the computer's timezone. A timezone name looks like
     `Europe/London` or `America/New_York`, and the bot uses it for every time in the file.
     - **Mac.** Run `readlink /etc/localtime`. It prints something like
       `/var/db/timezone/zoneinfo/Europe/London`. The part after `zoneinfo/` is the name.
     - **Windows.** Run `MSYS_NO_PATHCONV=1 tzutil /g`. It prints a Windows name like
       `GMT Standard Time`. Turn it into the matching name, for example `GMT Standard Time` is
       `Europe/London`, `Eastern Standard Time` is `America/New_York`, `Pacific Standard Time`
       is `America/Los_Angeles`, `AUS Eastern Standard Time` is `Australia/Sydney` and
       `W. Europe Standard Time` is `Europe/Berlin`. For any other, look it up in the Unicode
       list at https://github.com/unicode-org/cldr/blob/main/common/supplemental/windowsZones.xml
       and pick the city that matches where they live.
   - Change the line `timezone = "UTC"` to their name, for example `timezone = "Europe/London"`,
     and tell them which one you set and why. If the computer's timezone isn't where they
     live, ask which city to use.
   - **Windows only.** Tell them this file on their computer is only the copy you edit. Later,
     `scripts/push_config.sh` sends it to the server, where the bot reads it.

7. **The bot's tools (Mac only).** Run `uv sync`. Tell them it downloads Python and the bot's
   building blocks, and it takes a minute or two the first time. It should finish without a red
   error.

## Check

**Mac.** All of these pass.

- `uv run igdm keys` runs and lists the keys. They'll all say `missing` for now, and that's
  fine. What matters is that the bot starts.
- `"$SHELL" -lic 'command -v uv'` prints a path.
- `grep '^timezone' config.toml` shows their timezone.

**Windows.** All of these pass. You run them yourself, in the Git Bash shell Claude Code uses
for commands. If you ever need them to open Git Bash, say "Open the Start menu, type Git Bash,
and press Enter."

- `git --version` prints a version.
- `ssh -V` prints a version. It prints to the error stream, which is normal.
- `grep '^timezone' config.toml` shows their timezone.

When it passes, write `.setup-progress.json` with their `platform` and `"done": ["01-get-ready"]`
(see [../reference/progress.md](../reference/progress.md)). Tell them the computer is ready.
Then go to chapter 2 on a Mac, or chapter 13 on Windows.

## If it doesn't work

- **A new Terminal window says "uv: command not found".** The PATH line from the uv installer
  isn't in their shell's start-up file. Run `echo "$SHELL"` to see which shell they use, then add
  the line the installer printed to `~/.zshrc` (zsh) or `~/.bash_profile` (bash), and run the
  `"$SHELL" -lic 'command -v uv'` check again. `uv tool update-shell` can also add it.
- **`xcode-select --install` says the tools are already installed but git still fails.** Run
  `xcode-select -p`. If it prints nothing useful, the tools need resetting, which needs their Mac
  password. Never run `sudo` yourself. Ask them to open Terminal (press Command and Space, type
  Terminal, press Enter), then type `sudo xcode-select --reset` and press Enter. Tell them it asks
  for their Mac password, and nothing shows as they type it, which is normal. When they say it's
  done, run `git --version` again.
- **`uv sync` fails while downloading.** A VPN or a work network can block it. Ask them to turn
  the VPN off, then run it again.
- **"Permission denied" when running a script.** Run it with `bash` in front, like
  `bash scripts/ask_key.sh`.
- **`tzutil` prints a drive letter error on Windows.** Git Bash changed `/g` into a folder path.
  Use `MSYS_NO_PATHCONV=1 tzutil /g` as written, or run
  `powershell.exe -NoProfile -Command "(Get-TimeZone).Id"`, which prints the same Windows name.
- **The folder check finds the wrong folder.** Claude Code was started somewhere else. Ask them
  to reopen it in the project folder, as in step 4 (in Terminal or Git Bash with `/exit`, `cd`
  and `claude`, or in the desktop app's folder picker).
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot never runs on the Windows computer, so this chapter skips uv and `uv sync` there.
Windows only needs git and ssh (the tool for typing commands on the server), and both come with
Git for Windows. From now on, every `igdm` command runs on the server in this form:

```
ssh igdm 'cd app && ~/.local/bin/uv run igdm …'
```

That only works once the cloud chapter has set up the server, which is why chapter 13 comes next.
