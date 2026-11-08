# Chapter 4: The Meta app

## Why this matters

Meta only lets a program use Instagram through an app registered on its developer site. Here, an
"app" isn't something on their phone. It's a free entry on developers.facebook.com that says who
the program is and what it may do. Each creator makes their own, so they're its admin and nobody
else's app can switch them off.

A permission is one thing Meta lets the app do, like reading comments or sending DMs. This
chapter gives the app exactly the permissions the bot needs. Two of them aren't added by Meta's
buttons, and missing either one breaks the bot later, so the order below matters.

Keep the VPN off, and use the same Facebook account as in chapter 3. On Windows, Tailscale stays
on, because it isn't that kind of VPN and every server command needs it.

## Steps

Before you create anything, read the `04-meta-app` note in `.setup-progress.json`. If it says the
app is already made, never make a second one. Open the existing app and carry on after the last
step the note mentions.

Meta's app dashboard often returns little or no page text, so take screenshots there and work
from what you see. Meta changes labels often. If a label differs from what's written here, look
for the nearest match and tell them what you're clicking instead.

1. **Open the developer site.** Warn them about the Chrome permission for
   developers.facebook.com, then open https://developers.facebook.com/apps/.
   - If it asks them to log in, that's theirs. Remind them to use the Facebook account that's an
     admin of their Page.
   - If it asks them to register as a Meta developer (a "Get started" button, accepting terms, and
     a code by text or email), that's theirs too. It's free. Tell them what each screen asks and
     wait.
   - If their app list already shows an app they made for the bot, ask before making another.
   - The list may show a banner about "New app lifecycle tools". They can ignore it.

2. **Create the app.** Click the green "Create App" button at the top right. The wizard has five
   steps. They are App details, Use cases, Business, Requirements and Overview.

3. **App details.** Fill in "App name" yourself, with their OK. Any name works, like "Comment
   helper". Meta refuses names with its own brand words in them, so avoid Instagram, Insta, IG,
   Facebook, FB, Meta and WhatsApp. "App contact email" is filled in already. Click Next.

4. **Use cases.** The list opens on the "Featured" filter, and the one they need isn't there.
   Click the "All" filter (or "Content management"), then tick "Manage messaging & content on
   Instagram". Tick only that one.
   - Don't add "Get real-time notifications with Webhooks" or any other use case. The bot doesn't
     use webhooks, which are a way for Meta to call a web server, and the bot has none.
   - Click Next.

5. **Business.** Choose "I don't want to connect a business portfolio yet". A business portfolio
   is Meta's name for a company account, and the bot doesn't need one. Click Next.

6. **Requirements and Overview.** Read what each step says, tell them in a sentence, and click
   Next. If a step asks them to agree to terms, that click is theirs. On the last step, the button
   to finish is theirs too, because Meta may ask for their Facebook password. When the app's dashboard opens, write a note straight away, for example
   `"04-meta-app": "Meta app created, called Comment helper"`. This is what stops a second session
   from making a second app.

7. **Open the Instagram use case.** In the app dashboard's left menu, click "Use cases". On the
   Instagram row, click "Customize".

8. **Pick Facebook login.** It opens on the "API setup with Unified login" tab, with an orange
   warning that says "Business portfolio selection is mandatory". That's expected. Tell them not
   to worry about it, and click the "API setup with Facebook login" tab instead.
   - The tab says it "still works, but it's now legacy". That's fine. It's the one that works
     without a business portfolio, and it gives the bot a Page token that never expires.
   - The tabs are "Permissions and features", "API setup with Unified login", "API setup with
     Facebook login" and "Webhooks". Leave "Webhooks" alone.

9. **Click both permission buttons.** Under "1. Add required permissions" there are two groups,
   "Manage content on Instagram" and "Send messages on Instagram". Click both buttons.
   - "Add required content permissions" adds instagram_basic, instagram_content_publish,
     pages_read_engagement, business_management and pages_show_list.
   - "Add required messaging permissions" adds instagram_basic, instagram_manage_messages,
     pages_read_engagement, pages_show_list and business_management.
   - If a button reads "Go to permissions and features" instead, that group is already added.
   - Wait until each group shows as added, then write a note, for example
     `"04-meta-app": "Meta app created, called Comment helper, both permission buttons clicked"`.

10. **Add the two the buttons leave out.** Click the "Permissions and features" tab. Each row has
    an "Add" button.
    - Find `instagram_manage_comments` and click Add. Take care, because a lookalike sits nearby.
      It's `instagram_manage_comments` with no "business" in it, not
      `instagram_business_manage_comments`. The same goes for `instagram_basic`, not
      `instagram_business_basic`.
    - Find the feature "Business Asset User Profile Access" and click Add. The bot needs it to see
      commenters' names.
    - Each added row shows "Ready for testing". Then update the note, for example
      `"04-meta-app": "Meta app created, called Comment helper, buttons clicked, comments and profile access added"`.

11. **Leave it in development.** Tell them the app stays "In development". They don't need to
    publish it or send it to Meta's App Review, which is Meta's check for apps that strangers use.
    Because it's their own app and they're its admin, the bot can still DM their ordinary
    followers. The live test in chapter 11 proves it for them. If Meta's dashboard suggests App
    Review, publishing or adding webhooks, they can ignore it.

## Check

On the "Permissions and features" tab, these all show "Ready for testing". Take a screenshot and
check each one.

- `instagram_basic`
- `instagram_manage_comments` (not the `instagram_business_` one)
- `instagram_manage_messages`
- `pages_show_list`
- `pages_read_engagement`
- `business_management`
- "Business Asset User Profile Access"

`instagram_content_publish` will be there too, from the content button. That's fine.

When it passes, add `04-meta-app` to `done` in `.setup-progress.json`, keeping the note with the
app's name (see [../reference/progress.md](../reference/progress.md)). Tell them the app is ready,
and that next you'll connect the bot to it.

## If it doesn't work

- **"Manage messaging & content on Instagram" isn't in the list.** Check the "All" filter is
  picked, not "Featured". If it still isn't there, look for the nearest match about Instagram
  messaging and content, and tell them what you're choosing.
- **They added the Webhooks use case by mistake.** Leave it. It does no harm, as long as nothing
  is set up inside it.
- **The app name is refused.** Pick a plainer one without Meta's brand words.
- **A row has no "Add" button, or says it needs App Review.** Read what the row says and take a
  screenshot. A permission that's only "Ready for testing" is enough for their own account. Check
  the lookalike names again, because the `instagram_business_` ones belong to a different setup.
- **Facebook refuses a change.** Check the VPN is off, then wait about ten minutes.
- **The page is blank or shows no text.** Take a screenshot instead of reading the text. That's
  normal for Meta's dashboard.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md) and
  [../reference/meta-notes.md](../reference/meta-notes.md).
