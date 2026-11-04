# Design

## Brief

A situation room for the person who runs a cold email fleet.  It answers
first: what needs me today?  Then: where, since when, how sure, what to do,
and lets them approve the fix.  Readable in ten seconds, trustworthy in a
minute.

## Direction

Picked with the ui-ux-pro-max skill's style database (Swiss Modernism 2.0 +
Data-Dense Dashboard for monitoring products) and Anthropic's frontend-design
guidance, then pushed toward the product's own idea: an intelligence brief.

- Swiss grid and hairline rules instead of floating cards.  Square corners.
- One accent, signal amber, for what you can act on; everything else ink.
- Red, amber and green mean state only (P1, P2, fine).  Blocklisted is violet.
- The briefing is set as a document: slug line with a date-time group
  (012333Z NOV 26), BLUF, numbered paragraphs, assessment.
- Priority tags (P1/P2/P3) instead of coloured dots.
- Keyboard: 1-7 switch pages, Esc closes the drawer.

## Type

IBM Plex throughout, bundled with the app so it works offline.
Plex Sans for words, Plex Sans Condensed for headings and the numbers that
matter, Plex Mono for data: domains, times, counts in tables.

## Tokens (dark; light mirrors them)

| role    | dark      | light     |
|---------|-----------|-----------|
| bg      | `#0f1110` | `#f2f1ec` |
| surface | `#151816` | `#faf9f6` |
| rule    | `#262b27` | `#dcdad2` |
| ink     | `#e9e7e1` | `#171a18` |
| dim     | `#a19f97` | `#5d5f59` |
| signal  | `#f2a900` | `#b77d00` |
| p1      | `#ef4b3f` | `#c8352b` |
| p2      | `#e89b2b` | `#b6720a` |
| ok      | `#3f7d58` | `#4f8f68` |
| listed  | `#b48cff` | `#7a4fd6` |

## Icon

An envelope whose flap is a rank chevron, with a second chevron beneath:
email, and someone in charge of it.  Amber on graphite.
