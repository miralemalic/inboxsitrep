# Setup

About fifteen minutes from install to your first report in Slack or Telegram.
Everything runs on your computer. Inbox Sitrep only talks to your sequencer,
Slack, Telegram and, if you turn the analyst on, Claude.

## 1. Install

You need Python 3.10 or newer.

**Linux**

```
pipx install git+https://github.com/miralemalic/inboxsitrep
inboxsitrep app --install          # adds it to your applications menu
```

The window uses GTK and WebKit, which GNOME and KDE already have. If they're
missing, it opens in Chrome or Edge instead.

**macOS**

```
brew install pipx
pipx install "inboxsitrep[window] @ git+https://github.com/miralemalic/inboxsitrep"
inboxsitrep app --install          # adds Inbox Sitrep to ~/Applications
```

**Windows**

```
py -m pip install --user pipx
py -m pipx install "inboxsitrep[window] @ git+https://github.com/miralemalic/inboxsitrep"
inboxsitrep app --install          # adds Inbox Sitrep to the Start menu
```

The `[window]` part gives it a proper app window on macOS and Windows. Without
it, it opens in Chrome or Edge.

**The analyst** writes the reports and answers questions. It runs on your
[Claude Code](https://claude.com/claude-code) login, so install Claude Code
and log in once. A normal Claude subscription is enough. Everything else works
without it.

## 2. Add a workspace

Open Inbox Sitrep and go to **Settings → Workspaces**.

| Sequencer | What to enter |
|---|---|
| PlusVibe | the workspace id and an API key |
| Instantly | an API key (v2) |
| EmailBison | your instance address and an API key |

Press **Add**. It tests the key straight away with one small read and tells
you if the key or the workspace is wrong. The first check reads 31 days, which
takes a few minutes on a big workspace. After that it only reads what changed.

It won't change anything in the workspace until you allow it in Settings. Even
then, every change shows you what it will do and waits for you to approve it,
and you can undo it.

No API key handy? The first screen offers demo data to try it on.

## 3. Connect Slack

Reports go to a channel. Mention the app or send it a direct message and the
analyst replies in the thread.

1. **Settings → Slack → Create the Slack app.** Slack opens with the app
   already filled in. Pick your workspace, then **Next** and **Create**.
2. In Slack: **Install App** (left menu) → **Install to Workspace** →
   **Allow**. Copy the **Bot User OAuth Token** (starts with `xoxb-`) into
   Settings.
3. **Basic Information → App-Level Tokens → Generate Token and Scopes.** Call
   it `socket`, add the scope `connections:write`, press **Generate**, and copy
   the token (starts with `xapp-`) into Settings.
4. Type the channel for reports (for example `#deliverability`), then **Save**
   and **Send a test**.
5. Optional: **Basic Information → Display Information**, and upload the icon
   (`inboxsitrep/static/icon.svg`, or any 512×512 image).

Then try `@Inbox Sitrep which domains should I pause?` in the channel. Settings
shows **Connected** when the app is listening.

Why two tokens: each Slack workspace creates its own copy of the app, so there
is no central Inbox Sitrep server in between. Only the messages leave your
computer.

If you only want the reports and don't need to ask questions, paste an
incoming webhook under **Reports only, without the app** instead.

## 4. Connect Telegram

Reports come to your Telegram chat, and you can ask questions there too.

1. In Telegram, open **@BotFather** and send `/newbot`. Call it
   `Inbox Sitrep`. The username has to end in `bot`.
2. Copy the token BotFather gives you into **Settings → Telegram** and press
   **Save**. A six-digit pairing code appears.
3. Open your new bot in Telegram and send it that code. Settings changes to
   **Linked**, and from then on only that chat gets reports and answers.
4. Optional: in @BotFather, send `/setuserpic` and then the icon.

**Link another chat** in Settings gives you a new code and drops the old chat.

## 5. Choose when it reports

All of this is in **Settings**.

- **Daily report** at the times you set (08:00 and 17:00 to start with). Every
  workspace is checked fresh first, and one message covers them all.
- **Weekly report** on Saturday at 08:00, after Friday's sending is done.
- **Between reports:** urgent alerts only, checked every 15 minutes. That
  means tenant blocks, Gmail or Microsoft starting to refuse a domain, and
  failed authentication. Blocklists wait for the daily report.
- **Check every** few hours while the app is open.
- **Open at login**, so it runs without you remembering to start it.

It only watches while the computer is awake. To keep watch around the clock,
run `inboxsitrep schedule` on a Linux machine that stays on.
