# When they're stuck

When a Check fails, a command errors, or a page doesn't look the way a chapter says, don't guess
and don't give up. Do these four things, in order. Only move on to the next chapter once the Check
passes.

## 1. Read the exact error, the logs and the page

- The full error from the command, word for word.
- The bot's log.
  - **Mac:** `tail -n 100 logs/igdm.log`. If the background agent is installed, also look at
    `logs/launchd.err` and `logs/dashboard.err`.
  - **Server:** `ssh igdm 'tail -n 100 app/logs/igdm.log'`, and for the services
    `ssh igdm 'journalctl -u igdm -n 50'` or `ssh igdm 'journalctl -u igdm-dashboard -n 50'`.
- Before reading or screenshotting the page they're on, check the hands-off rule in
  [chrome.md](chrome.md). If a secret may be showing, use tab titles only.
- The page they're on. Read its text, or take a screenshot if the text is empty. If you can't see
  their browser, ask them to describe it or drag a screenshot into the chat with no secret showing.
- Which keys are set, with `uv run igdm keys` (or the server form). Never open `.env`.
- Logs like `logs/launchd.err`, `logs/igdm.log` and `journalctl` output can hold a token-like
  string, such as a long run of letters and digits after `access_token=`, `key=` or `Bearer`.
  Never repeat such a string back to them, in your explanation or in a search. Quote the error
  around it and write "[hidden]" in its place.

## 2. Check the known snags first

- The chapter's own "If it doesn't work" part.
- [meta-notes.md](meta-notes.md) for Meta, Facebook and Instagram problems. These come up most
  often, and the fixes there are ones that actually worked.
  - `code=190` with "The session has been invalidated" means the connection to Meta has to be
    made again, with a new token and `igdm connect` (chapter 5).
  - `code=200` with "Cannot call API for app … on behalf of user", or "API access blocked",
    means they need to log in to facebook.com in Chrome and pass the security check it shows.
    Meta's developer dashboard usually shows nothing about it.
  - `(#100) The Page is not linked to an Instagram account or the linked IG account is not a
    professional account` means chapters 2 and 3 need another look.
  - When a comment never shows up, Instagram may be holding it back from the API. Compare the
    reel's comment count with what the bot sees before blaming the bot.
- A VPN. If they have one on, ask them to turn it off and try again. Facebook, ntfy and Tailscale
  all had trouble with one. Tailscale itself isn't that kind of VPN. If the bot runs on the
  server, Tailscale stays on, because every server command goes through it.

## 3. Search the web

Search for the exact error text and for the current state of the screen they're on. Good places
are the Meta developer community, Stack Overflow, Meta's changelog, and the provider's own help
pages. Prefer recent answers, because Meta changes its screens often.

## 4. Tell them, with the evidence, then fix it

Say it in plain words, in this shape:

> Here's what's going on. [One or two sentences about the problem, in plain words.]
>
> Here's how I know. [The error line, what the page shows, or the source you found, with its
> link.]
>
> The most likely fix is [first fix]. If that doesn't do it, the next thing to try is
> [second fix].
>
> Let's try the first one. [The first step of it, with where and what they'll see.]

Then walk them through the fix one step at a time, and run the chapter's Check again. If it still
fails, go back to step 1 with the new error.

If after all that you can't find a fix, say so honestly. Tell them what you tried, what you found,
and what they could try later. Write a short note about it in the progress file, with no secrets,
so the next session knows where things stand. There is no support line, so be as useful as you can.
