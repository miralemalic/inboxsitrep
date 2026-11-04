# Changelog

## 1.0.0 (2026-11-04), first public release

- **`inboxsitrep demo`**: three simulated workspaces, a third each on Google
  Workspace, Microsoft 365 and SMTP, twelve campaigns apiece with copy, each
  with its own trouble.
- **Reports lead with status**: every workspace RED, AMBER or GREEN with its
  worst problem, the same on the All workspaces page.  Yesterday's full day
  unless today is a quarter sent, so a European morning report on US sending
  is complete.  Positives per 1,000 by sender infrastructure.
- **Weekly SITREP on Saturday at 08:00** by default, after a full US Friday.
- **Campaigns running dry**: named when one has under two days of leads, even
  when the workspace has a week left.
- **One story, one line**: a Gmail cascade's dead domains, a throttle that
  became a refusal and a blocklisted family losing Gmail replies are no longer
  told twice.
- **Campaigns are judged against the rest of the workspace**, so a strong one
  does not raise its own bar.
- Fixed: open tracking read as on for every PlusVibe campaign (PlusVibe says 0
  and 1), and the copy check failing on it; the MCP campaigns tool failing;
  simulated changes missing from the Changes page.
- Settings: one control for what comes between reports.
- A blocklisted family is Important, not Urgent, and its assessment says to
  request removal and keep sending, as the plan does.  Report dates read
  "27 Oct"; report titles no longer use a date format Windows lacks.
- New screenshots, tour GIF and a SITREP image, all from `inboxsitrep demo`.
- **A cleaner app.**  The Briefing reads in the order you act: decisions,
  the numbers, your mailboxes against their providers, the fleet day by day,
  the report last (folded unless the analyst wrote it); no more repeats.
  Every workspace page shows its RED, AMBER or GREEN, the same as the
  SITREP.  The sidebar shows counts, not shortcut digits, in three groups.
  Dates read "2 Nov" everywhere.  Settings: reports first, then where they
  go, then this computer; setup steps fold away once connected; an unsaved
  change says so.
- **Custom dates work**: a range applies as soon as both dates are set, the
  pickers stop at the days collected, and a range reaching past them says
  so instead of quietly showing less.
- **Fixed: replies and bounces lost when a campaign is deleted.**  PlusVibe
  deletes a campaign's leads with it, and replies and bounces were read only
  from leads, so domains whose campaigns were gone showed thousands of sends
  and nothing else ("Silent").  The mailboxes' own counts now fill in what
  the leads cannot account for.
- A domain with sends and still no reply or bounce at all is "No reply or
  bounce data", under its own filter, not Stop; its numbers show as missing.
- **A domain's verdict is on its latest 1,000 sends**, whatever the range
  picked: a 14-day average no longer hides a problem that started four days
  ago, and a 7-day range no longer leaves most domains unjudged.  The range
  still sets the numbers shown.
- **Stop means refused, not just bounced.**  A domain is stopped when 4% or
  more of its mail is refused for who sent it and it earns little; over 5%
  bounced with 2-4% refused is "list and domain", under 2% a bad list.  On a
  real workspace this took Stop from 34 domains to 1.
- **Refused and Bad addresses** replace "sender bounces" and "list bounces"
  everywhere; "Mailboxes down" is "Disconnected mailboxes".
- **Positives unknown, not zero**, where a campaign was deleted with its
  leads: those domains are judged on replies and left out of the baseline.
- **One progress bar** with the time left, from how long each workspace took
  last time; a run of several workspaces no longer looks finished after the
  first.  "Check this workspace" sits in each
  workspace's header; "Check all" on the All workspaces page.
- Fixed: pressing Stop during a check ended the background watch for good
  (no more checks or reports until a restart).  Stop now ends the check, and
  nothing that goes wrong in one round can end the watch.
- **Your rules, in one file.**  rules.ini beside the settings holds every
  threshold: what stops, fixes or delists a domain, what makes a campaign a
  winner or a loser (and an optional hard reply-rate floor), how many spare
  domains to keep, mailbox volume, lead warnings, and domains to protect.
  Written with the defaults and a comment on every line; the verdict wording
  follows it.
- **Every message says which workspace first.**  The daily report's first
  line (what a phone shows) names the red and amber workspaces; the weekly
  names the workspaces it covers; new-problem messages name each workspace.
  Alerts say what to do in one line.
- **A shorter daily report.**  Each workspace once: its status, yesterday in
  one line, its top decisions.  With more than four workspaces, amber ones
  get a line each and green ones share one.  Two workspaces: about 1,600
  characters; fourteen: about 3,000.
- On a narrow window or a phone the page no longer scrolls sideways.
- README and setup guide rewritten in plain words.  New thresholds sourced in
  docs/numbers.md.
- Domains says when burn rate will appear (after the next check), and a
  verdict's tooltip gives the sends and dates it was judged on.
- **New icon**: the radar, with a letter as the contact it tracks.
- `inboxsitrep demo` starts clean: no changes or reports left from an earlier run.
- **Reserve and burn rate**, at the top of the Domains page: how many domains were lost in the last 30
  days, how many warmed spares are ready (mailboxes warming in no campaign),
  how many weeks they last, and how many to buy and warm to cover a month of
  losses or hold 10% in reserve.  In the weekly SITREP, in the daily one when
  it runs short, and on the All workspaces page.  PlusVibe workspaces.
- **Microsoft's 5.7.515** (domains failing its authentication rules for
  high-volume senders, rejected outright since November 2025) and Gmail's
  5.7.27 now read as authentication problems, so the fix says "DNS".
- **Mailboxes pushed too hard**: domains averaging over 50 cold sends a day
  per mailbox get a recommended cap of 30.  In 2026 field data, 20-49 a day
  replied at 5.7% and 100+ at 1.4% with bounces four times higher.
- Plain words throughout: no "fleet", no talk of how the app works inside.
  Hide names (for screenshots of real data) moved to Settings; R still
  toggles it.
- **All workspaces: check them all, or one**, from that page, with which one
  is running, a Stop, and when each was last checked.  The sidebar button
  says "Check all" when there is more than one.
- The review of a recommended step shows the step at once, while the
  workspace is read, and offers "say it differently" once there is
  something to compare.
- Demo settings run without the analyst, so trying the app spends no Claude
  usage on made-up workspaces.
- The log, the lock and the record of reports sent live beside the settings
  file, so a demo or test run never writes into the real log or marks a real
  report as sent.

## 0.6.0 (2026-11-03)

- **Ask it in Slack or Telegram**: mention the app or message the bot; the
  analyst answers.  Socket Mode and long polling, so nothing has to be hosted.
  Telegram pairs to one chat with a code.
- **All workspaces**: one overview page, and one message per check run.
- **Merge fields**: the copy check reads a sample of each active campaign's
  leads and flags fields they don't have or leave empty with no fallback.
- **Campaigns** are judged on reply rate and bounce rate only.
- **Radar icon.**
- **Copy page**, and the copy check catches link shorteners, shouting subjects
  and one version for everyone.
- **Campaigns**: nothing over its bounce limit is a winner; judge on positive
  replies or all replies (Settings).
- **Listings**: removal requests for what still delivers, pauses only for what
  receivers refuse.  Replies under 1% count as low whatever the workspace does.
- **By sending infrastructure**: Google Workspace, Microsoft 365 and SMTP side
  by side.  A workspace that has stopped sending says so.
- Email and WhatsApp reports removed: Slack and Telegram.

## 0.5.0 (2026-11-03)

- **A verdict for every domain**: healthy, strong, blocklisted (with how to get
  off each list), bad list, list and domain, copy refused, fix authentication,
  refused, burned, account blocked, silent, underperforming, too early.  Bounces
  are judged from 500 sends, replies from 1,000, and positive replies against
  the rest of the workspace.  A blocklist is something to get off, not a reason
  to stop a domain that still earns.  The ladder follows Eric Nowoslawski's
  coldoutboundskills.
- **Bounces by whose fault**: the list, the domain, the copy, or neither.
- **Campaign reviews**: winners to scale, losers to stop, bad lists, against the
  workspace's own positive-reply rate, tested so luck is not a winner.
- **Any range**: today live from PlusVibe, the last 7, 14 or 30 days, or two
  dates.  Workspaces are now read 31 days back.
- **A guard on every change**: the review shows each domain's verdict, and
  changing a domain that does well needs a second yes.
- Logs page; every incident takes an instruction in your own words.

## 0.4.1 (2026-11-03)

- **Say what you want.** Change a recommended step in your own words, or ask for
  a change from scratch on the Changes page; the analyst turns it into an
  allowed change on this workspace's domains, previewed before anything happens.
  In Claude Code, `propose_change` does the same.
- **Warmup and daily limits.** A pause can pause warmup too, or set a daily limit
  instead of 0, on PlusVibe, Instantly and EmailBison; undo restores each
  mailbox's limit and turns warmup back on only where it was on.
- **Allowing changes works.** The setting was never saved; it is now a clear
  Read only / Changes allowed choice per workspace, and the review box can
  allow changes for its workspace on the spot.
- **Slack setup steps** in Settings; links from the app open in the browser.

## 0.4.0 (2026-11-03)

- **Report times.** Set 08:00 and 16:00 and every workspace is checked afresh
  then, with one report for all of them to Slack, email and WhatsApp. The run
  starts as early as the last checks took, so the report arrives on time. A
  workspace whose check failed says so above its last good report.
- **What is new.** Reports mark incidents that appeared since the last report.
- **Open at login**, so checks and reports happen without opening the app.
- **Test a workspace's key** with one small read, and see what the sequencer
  said when it refuses.
- **Try it on a simulated fleet** from the first screen, without a key.
- **A pause is read back** after it is applied, to confirm the sequencer took it.
- **One count everywhere.** The report folds incidents by the same rules as the
  app, so the two never disagree; a listed family is one line.
- **Plans in plain words**, and a plan whose domains are already covered names
  the incident that covers them, as a link.
- **Slack and WhatsApp markup** instead of raw Markdown.
- **A narrow window** shows the pages as tabs; the sidebar used to vanish.
- Fixes: bounce texts counted once on the first incremental read of an older
  database; DNS listings survive between collections; what is kept between
  reads is pruned to the window; the app log rotates.

## 0.3.0 (2026-11-02)

The desktop app, the Claude Code plugin and MCP server, Instantly and
EmailBison, approved changes with undo, notifications, and the analyst's exam.
