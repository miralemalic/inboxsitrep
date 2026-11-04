# Where the numbers come from

Every rate in `sim.py` and every threshold in `detect.py` was measured on a
real agency fleet in August-October 2026: eight PlusVibe workspaces, around
650 domains in the largest, three million sends a month.  Client names are
left out.

| number | used for | measured |
|---|---|---|
| 300 sends a day per domain | simulated volume | the standard setup: 100 mailboxes x 3 a day per domain |
| ~2% blended bounce rate | simulated baseline | 2.14% over 3.02M sends in 30 days, largest workspace |
| 7-11% bounces in the "other" bucket | simulated baseline | self-hosted and small-ISP inboxes, two workspaces |
| 17% of replies are positive | simulated positives | 7,367 positives out of 43,309 replies, 30 days |
| Gmail replies 0.47% vs Microsoft 2.53% | the Gmail gap; fleets filed as spam at Gmail | two workspaces with no domains, lists or copy in common: 0.47% / 0.48% at Gmail, 2.53% / 2.16% at Microsoft |
| Gmail bounces lower than Microsoft's on the same fleet | why quiet spam needs replies, not bounces | 1.21% vs 2.31% in one of those workspaces |
| a dead domain: Gmail refuses 87-100% | `DEAD` threshold; simulated burns at 90% | nine domains of one family, September |
| refusal rates were 39-79% or under 8%, nothing between | where the `DEAD` line can safely sit (30%) | 97,935 mailboxes across seven workspaces, one day |
| Microsoft: zero refusals from the same dead domains | "stop Gmail, not the domain" | the same nine domains, a whole week |
| episodes: 48.9% -> 75.2% -> 26.4% -> 0.6% with volume flat | episodes clear; `BURN_DAYS` = 3 | four days on the largest workspace, ~326 sends a day throughout |
| a cascade: 9 of 30 look-alike domains in 11 days, one a day then two | the cascade detector, simulated cascades | one family, September |
| 97-99% of Spamhaus/SURBL bounces name the sending domain | trusting bounce text over DNS lookups | two workspaces |
| Spamhaus refuses shared resolvers (`127.255.255.254`) | why DNS lookups alone can't be trusted | the network this was built on |
| Apple refuses 78% with `[CS01]`, spread evenly over 36 domains | the Apple detector and simulation | 639 sends to iCloud addresses, 500 refused; 6.4% of sends but 84% of bounces |
| ~35 domains at 5-8% bounces, all recipient-side, same volumes | the bad-list detector | one day, the largest workspace |
| Google warmup health averaging 69-71, Microsoft 97-100 | simulated health scores | 74,784 mailboxes, two workspaces |
| 5.7.237 tenant blocks: 16,580 + 3,604 bounces on 96 domains in 9 days | the tenant-block detector; excluded from Gmail refusals | two workspaces, October |
| Spectrum (AUP#) refused 2,124 + 1,270 messages; Mimecast 960; Sophos 1,179 | one receiver refusing many domains: 50 refusals from 5+ domains in a week | two workspaces, three weeks |
| "detected as spam", "high probability of spam": 265 in a week over 29 domains | copy flagged: 30+ refusals from 5+ domains, and 0.1%+ of sends | one workspace |
| 815 + 641 Microsoft S3150 / 5.7.708 refusals, spread thin | Microsoft refusal needs 20%+ of a domain's Microsoft sends, 10+ of them, two days | two workspaces |
| 100 of 50,072 mailboxes in `ALERT`, 600 with warmup off | the dead-mailbox check | one workspace, one day |
| the bounce guard paused active campaigns at a 5% limit on the day tenant blocks peaked | the bounce-guard check | one workspace, 28 October |
| ~1,500 unsent leads per active campaign | lead runway: warn under 5 days of new contacts | one workspace, 1 November |
| 7 domains with a registrar's parking MX and a private-range SPF | the parked-domain DNS check | one workspace |

Thresholds without field data behind them, and why they sit where they do:

| number | used for | why |
|---|---|---|
| 421 4.7.28: 5+ deferrals and 3%+ of Gmail sends in two days | Gmail throttle | Gmail's documented rate-limit response; none in our bounce logs yet |
| warmup score down 25+ points to under 60; back over 70 to resume | warmup slide | below the fleet's 69-71 average, and far enough from the 2-point daily noise that it can't trip by chance |
| TSS04 / "too many complaints": 3+ in three days | complaints | rare in our logs (3 bounces) and never harmless |
| a link domain named in bounces from 3+ senders, or 10+ bounces | listed link | the field showed only 4 bounces naming a domain other than the sender's, all isolated |
| 50+ sends a day falling under 10% for two working days | idle domains | days the whole fleet sends under 30% of its median (weekends) are skipped |
| live alerts: 10 tenant blocks, 10 Gmail reputation refusals, 3 blocklist namings in two hours | live mode | an hour of a dead domain at 300 sends a day produces about 11 refusals |
| Stop at 4%+ refused; over 5% bounced with 2-4% refused is list and domain, under 2% a bad list | domain verdicts | about 1% bad addresses is normal noise on a verified list; the usual 5% line pulled healthy domains whose bounces were mostly bad addresses |
| each verdict on the domain's latest 1,000 sends, over at least 3 sending days | domain verdicts | a 14-day average hid a 6-7% bounce spike that had started four days earlier on 30 domains |
| over 50 cold sends a day per mailbox; cap at 30 | mailbox volume | 2026 benchmarks: 20-49 a day replied at 5.7%, 100+ at 1.4% with bounces 4.3x higher ([Saleshandy, 30.31M emails](https://www.saleshandy.com/blog/cold-email-statistics/)) |
| keep a month of losses, and never under 10% of sending domains, warmed in reserve | reserve and burn rate | at scale 10-20% of domains burn a month, and the advice is a 20-25% reserve ([Maildeck, July 2026](https://maildeck.co/blog/cold-email-domain-rotation)); measuring your own losses replaces the rule of thumb, with 10% as the floor |
| Microsoft 550 5.7.515 counted as authentication | broken authentication | Microsoft rejects consumer mail from domains sending 5,000+ a day without aligned SPF, DKIM and DMARC since November 2025 ([Microsoft](https://support.microsoft.com/en-us/outlook/fix-ndr-error-550-5-7-515-in-outlook-com)) |

The detectors were written from the same lessons the simulator encodes.
The simulator therefore shows that the logic does what it says; the read-only
runs on the real workspaces are what show it finds real problems.
