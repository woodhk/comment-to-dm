# Chapter 7: AI key

## Why this matters

The bot uses an AI in two places. It writes a short reply when someone writes back with something
the prewritten replies don't cover. And if there's no Jev key from chapter 6, it also uses the AI
to spot who is asking for the material.

Suggest OpenAI. Without a Jev key, OpenAI is the only way the bot can spot requests, so it's
needed anyway. It uses a very cheap model, and a creator's bill is usually cents a day. Gemini,
from Google, is only an alternative for someone who has a Jev key and would rather use Google.
Its prepaid credits can run out with a confusing error, so it isn't the first choice.

An API key is a long password that lets the bot use the AI in their name. It goes into their keys
file through the private window, like every other secret.

## Steps

Do these one at a time.

1. **Say what's coming, and what it costs.** Tell them before anything is paid:

   > Next we'll get an OpenAI key. OpenAI is the company behind ChatGPT, and the bot uses one of
   > its smallest, cheapest models. You'll need to add a payment card and buy a small amount of
   > credit, usually US$5. The bot uses very little, often just cents a day. We'll also set a
   > monthly limit, so it can never spend more than you're happy with.

   If they have a Jev key and want Gemini instead, go to "Gemini instead" below. If they have no
   Jev key, OpenAI is needed, so explain that kindly.

2. **Log in to OpenAI.** Open https://platform.openai.com/api-keys. Warn them first that Chrome
   may ask whether you can use platform.openai.com. The login or sign-up is theirs. A ChatGPT
   login works here too, but the API has its own billing, separate from any ChatGPT plan. Tell
   them that in a sentence, so they aren't surprised.

3. **Add a card and some credit.** Open https://platform.openai.com/settings/organization/billing/overview.
   Labels change, so read the page and name the button that's there. It's usually "Add payment
   details", or "Add to credit balance" once a card is saved.
   - Before anything here, read the `07-ai-key` note. If it says "about to buy OpenAI credit" or
     that credit was bought, the payment may already have gone through. Read the credit balance
     on this page with them before you offer to buy again.
   - Adding the card and choosing the amount are theirs. Never fill in a card number.
   - Suggest the smallest amount it offers, often US$5, and suggest leaving automatic recharge off
     for now. They can turn it on later.
   - Right before they click to pay, write the note `"07-ai-key": "about to buy OpenAI credit"`.
   - Wait until they say it's paid. Then update the note straight away, like
     `"07-ai-key": "OpenAI card added and US$5 credit bought"`. Never write card details.

4. **Set a monthly limit.** Open https://platform.openai.com/settings/organization/limits. Look
   for a monthly budget, or a usage limit, and an email alert. Read the page and tell them where
   it is.
   - Setting the amount is theirs. Suggest something small, like US$5 or US$10 a month, and an
     email alert at about half of it.
   - If the page has no monthly budget, explain that the prepaid credit is the limit anyway. The
     bot stops using OpenAI when the credit runs out, and nothing more is charged unless
     automatic recharge is on.
   - Update the note, like `"07-ai-key": "OpenAI card added, US$5 credit, US$10 monthly budget"`.

5. **Make the key.** Open https://platform.openai.com/api-keys again.
   - Point them to the "Create new secret key" button. In the dialog you may fill in the name,
     `igdm`, because that isn't secret. Leave the project and permissions as they are.
   - Run `bash scripts/ask_key.sh OPENAI_API_KEY`, and say the words from
     [../reference/who-does-what.md](../reference/who-does-what.md) about the window. On a Mac,
     if this is their first window, warn them about the "control Terminal" question first.
   - Then tell them to click "Create secret key", copy the key from the dialog (there's a Copy
     button next to it), paste it into the window and press Enter. OpenAI only shows the key
     once, so they paste it before closing the dialog.
   - From the moment you ask them to click "Create secret key", follow the hands-off rule in
     [../reference/chrome.md](../reference/chrome.md). Make no page reads, finds or screenshots of
     that tab until they say it's saved and they've clicked "Done" to close the dialog.
   - When they say it's saved, run `uv run igdm keys`. `OPENAI_API_KEY` should say `set`.

6. **A reel for the check.** `igdm check` needs a reel in the settings file, because it looks the
   reel up on Instagram and tests the checker with comments built from its keyword. If chapter 9
   has already happened, the reel is there, so skip this step.
   - Otherwise, ask for four things now, and say you'll carry them into chapter 9 so they don't
     answer twice.
     1. The link to the reel they want the bot to work on first. In the Instagram app they tap
        the reel, then the share arrow, then "Copy link", and send it to you. It looks like
        `https://www.instagram.com/reel/ABC123xyz/`. Drop anything after a `?` in the link.
     2. The word people comment to get the material, like `GUIDE`.
     3. What they're giving away, in a sentence, like "my free 5-day meal plan with a shopping
        list".
     4. The same thing in a few words, to finish the first DM, like "the meal plan". The first DM
        reads "just drop your email and i'll send you the meal plan".
   - In `config.toml`, in the `[[reels]]` block at the bottom, set `url`, `keyword`, `offer` (the
     sentence) and `dm_offer` (the few words) to their answers. Leave the rest for chapter 9.
   - Write a note, like `"09-make-it-yours": "reel link, keyword, offer and dm_offer added in
     chapter 7, read back in chapter 9"`. Chapter 9 reads them back so they can change them.

7. **Run the check.** Run `uv run igdm check`. It reads Instagram and asks the checker about three
   sample comments. It sends nothing. Say what each line means in plain words.
   - `api: facebook_login (Page …, DMs via page)` shows the Facebook Page it uses.
   - `account: @… (id …)` should be their Instagram username.
   - `requests checked by: jev (jev-latest)` means Jev is spotting requests. If it says
     `requests checked by: openai (gpt-6-luna), no Jev key`, OpenAI is doing it. Either is fine.
     Tell them which one it is.
   - `reel first: media id …, N comments` means the bot found their reel.
   - Three `sample` lines. The first two are their keyword and something like "guide please 🙏",
     and say `(should be high)`. The third, "nice video but the music is too loud", says
     `(should be low)`. A score is how sure the checker is, from 0 to 1. Above the threshold
     (0.8 unless changed) counts as a request.
   - `conversations in the last 7 days: N` means the bot can read their DMs.

### Gemini instead

Only if they have a Jev key set (chapter 6) and would rather use Google. Without Jev, the bot
still needs OpenAI to spot requests, so Gemini alone won't work.

1. Warn them before they start:

   > Gemini works on prepaid credits. If they run out, the bot gets an error that says
   > "402 RESOURCE_EXHAUSTED" and "Your prepayment credits are depleted". It isn't obvious what it
   > means, so if you ever see it, you need to top up in Google AI Studio.

2. Open https://aistudio.google.com/apikey. The login is theirs. Point them to "Create API key".
   Run `bash scripts/ask_key.sh GEMINI_API_KEY`, then hands off the tab (as in step 5) until they
   say it's saved. Run `uv run igdm keys`. `GEMINI_API_KEY` should say `set`.
3. Billing is theirs. If AI Studio asks them to set up billing or buy credits, tell them first,
   and write a note after any payment.
4. In `config.toml`, under `[llm]`, set `provider = "gemini"` and set `model` to a current Gemini
   Flash model. Check Google's model list for the name, because they change. The settings file
   shows an example in its comment.
5. Do step 6 if there's no reel yet, then step 7. `igdm check` tests Jev here, not Gemini. The
   Gemini key gets its first real use when the bot writes a reply, in the live test.

## Check

`uv run igdm check` finishes with no `ERROR` lines, the reel line shows their reel, and none of
the `sample` lines end with `<- not as expected`. When a line ends like that, the command exits
with code 1, which counts as a failed check.

- With OpenAI and no Jev key, the scores also prove the OpenAI key works.
- With a Jev key, the scores prove Jev works. The OpenAI key gets its first real use when the bot
  writes a reply, in the live test.

Tell them it worked in a sentence, for example "Your AI key works, and the bot can tell a request
from a normal comment." Add `07-ai-key` to `done` in `.setup-progress.json`, keeping the payment
note. Then go to chapter 8.

## If it doesn't work

- **`error: nothing to spot requests with`.** Neither `TYPESAFE_API_KEY` nor `OPENAI_API_KEY` is
  set. Run `uv run igdm keys` to see which is missing, and put the key in again.
- **`reel first: ERROR could not resolve …`.** The reel link is wrong, or the reel isn't on the
  Instagram account the bot is connected to. Ask them to copy the link again from the app. If the
  sample lines are missing, this is why, because the check needs a reel it can find.
- **A `sample` line says `ERROR` with `insufficient_quota`, `429` or something about billing.**
  The OpenAI account has no credit yet, or the payment is still going through. Check the billing
  page, then wait a few minutes and run the check again.
- **A `sample` line says `ERROR` with `401` or "Incorrect API key".** The key was copied wrong.
  They make a new one (step 5) and put it in again.
- **A `sample` line ends with `<- not as expected`.** One score was on the wrong side of the threshold (0.8 unless changed). Run
  the check again, since one odd score happens. If it keeps failing, look at the keyword and offer
  in `config.toml`. A very common word as a keyword, like `yes`, confuses the checker. Talk it
  through with them, and pick a clearer keyword if they're happy to.
- **`402 RESOURCE_EXHAUSTED` from Gemini.** The prepaid credits ran out. They top up in Google AI
  Studio, or switch `[llm]` back to `provider = "openai"` with `model = "gpt-6-luna"` and an
  OpenAI key.
- **`code=190` or `code=200` in the first lines.** That's Meta, not the AI. See
  [../reference/meta-notes.md](../reference/meta-notes.md).
- **They pasted the key into the chat.** Don't repeat it. Tell them it's no longer private. They
  delete it on https://platform.openai.com/api-keys and make a new one, which goes in through the
  window.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot runs on the server, so the key goes into the server's keys file, and the check runs
there.

- The key goes in with `bash scripts/ask_key.sh OPENAI_API_KEY --server` (or `GEMINI_API_KEY`).
  The window is a PowerShell window, and it asks the same way.
- Confirm it with `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
- For step 6, edit the local `config.toml`, then send it with `bash scripts/push_config.sh`. It
  checks the file on the server before it replaces the old one.
- Run the check with `ssh igdm 'cd app && ~/.local/bin/uv run igdm check'`. It prints the same
  lines.
