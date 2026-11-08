# Chapter 6: Jev key (optional)

## Why this matters

Every comment on their reel needs one quick decision. Is this person asking for the material, or
just saying something nice? Jev is a small AI from a company called TypeSafe that makes that
decision. It's fast and very cheap, about US$0.0001 a comment.

Jev is optional. Without a Jev key, the bot asks OpenAI the same question instead, with a very
cheap model, using the OpenAI key from chapter 7. Both work well. So this chapter is short, and
if Jev isn't open to them, you skip it without any fuss.

## Steps

1. **Ask whether they have one.** Ask them in plain words:

   > The bot needs a way to tell when someone is asking for your material. There are two ways to
   > do that. One is a small AI called Jev, from a company called TypeSafe. The other is OpenAI,
   > which you'll set up in the next chapter anyway. Jev is optional. Do you already have a Jev
   > key, or would you like to see if you can get one?

   - If they say no, or they'd rather not, go to "Skipping it" below.
   - If they already have a key, go to step 3.

2. **Look at the sign-up together.** Open https://typesafe.ai. Warn them first that Chrome may ask
   whether you can use the site (see [../reference/chrome.md](../reference/chrome.md)).
   - Read the page and look for a way to sign up or get an API key. An API key is a long password
     that lets the bot use a service in their name.
   - If the page says Jev is in early access, has a waitlist, or the sign-up page is closed to them
     (a "403" or "Forbidden" page counts), tell them that's fine and go to "Skipping it". Don't
     ask them to join a waitlist unless they want to.
   - If they can sign up, the sign-up and any login is theirs. If it asks for a card or a payment,
     tell them before it shows, and remember that paying is always theirs
     (see [../reference/who-does-what.md](../reference/who-does-what.md)). Write a note in
     `.setup-progress.json` straight after any payment, like `"06-jev": "TypeSafe account made, card
     added"`.
   - Then find the page where they make a key. Tell them where the button is. Making the key is
     theirs to click, because the key shows on screen once it's made.

3. **The key goes in.** Follow the hands-off rule in [../reference/chrome.md](../reference/chrome.md)
   from the moment you ask them to make or reveal the key.
   - Run `bash scripts/ask_key.sh TYPESAFE_API_KEY` and say the words from
     [../reference/who-does-what.md](../reference/who-does-what.md) about the window.
   - Tell them to make or copy the key on the TypeSafe page and paste it into the window, then
     press Enter.
   - Make no page reads, finds or screenshots of that tab until they say it's saved and they've
     closed the key dialog or left the page.
   - When they say it's saved, run `uv run igdm keys`. `TYPESAFE_API_KEY` should say `set`.

### Skipping it

Say something like this, then go to the Check:

> No problem at all. The bot will use OpenAI to spot requests instead, and it works just as well
> for this. We'll set up OpenAI in the next chapter.

Write the note `"06-jev": "skipped, no Jev key, using OpenAI"`.

## Check

- **With a key.** `uv run igdm keys` shows `TYPESAFE_API_KEY: set`. The key gets its real test in
  chapter 7, when `igdm check` says which checker it used.
- **Skipped.** The note says so. Nothing else to check.

Add `06-jev` to `done` in `.setup-progress.json` (see
[../reference/progress.md](../reference/progress.md)). Then go to chapter 7.

## If it doesn't work

- **The window doesn't open.** Use `--print`, as in
  [../reference/who-does-what.md](../reference/who-does-what.md).
- **`igdm keys` still says `missing`.** Ask what the window said. If it said "Nothing was pasted,
  so nothing changed", the paste didn't land. Open the window again and ask them to paste, then
  press Enter.
- **They're not sure the key works.** That's fine for now. Chapter 7's `igdm check` uses it, and
  shows an error next to the sample comments if TypeSafe refuses it.
- **They pasted the key into the chat.** Don't repeat it. Tell them it's no longer private, and
  help them make a new one on the TypeSafe page, then put the new one in through the window.
- **They change their mind halfway.** Skip it, as above. They can add a Jev key any time later
  with the same window, and the bot switches to Jev by itself on its next start.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The key goes straight into the server's keys file.

- Run `bash scripts/ask_key.sh TYPESAFE_API_KEY --server`. The window that opens is a PowerShell
  window, and it asks for the key the same way.
- Confirm it with `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
