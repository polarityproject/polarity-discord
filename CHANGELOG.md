# Changelog

Releases of polarity-discord, the Discord bot for Polarity's public rooms. Newest
first; the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

### Added

- Each room lists its tables, from the rooms API's `table_list` (keepers
  from 0.4.0): each table someone sits at with its rules, its two seated
  players with their flags and characters, and whether it is playing, with
  the score. Tables nobody sits at are counted after them ("2 Open Tables").
  Players not seated are listed after the tables. Rooms from older keepers
  show as before (table 1's rules).
- Polarity's names: the bot reads each room's `client_version` (or
  `sfvr_version`, from keepers before 0.4.0), takes update info whose
  product is Polarity or SFVR, and defaults to polaritymod.link and the
  rooms API at api.polaritymod.link. Its compose project is `polarity-discord`.
- The message is edited at every look (`REFRESH_EVERY` 20, was 300), so its
  "last checked" time shows the bot is polling. A `discord.env` that still
  says `REFRESH_EVERY=300` keeps the old pace.
- `TRACE=1` logs every look: each call's time, how old the rooms list is,
  and what was done to the message. Slow calls (5 s), slow looks (10 s) and
  a rooms list not moving for 55 s are logged anyway.
- Only the latest release's rooms are listed by default (`ROOM_VERSIONS`
  `latest`): the game lists only its own version's rooms, so others could not
  be found. The latest release is read from the Polarity site's update info every
  5 minutes; the heading names it and counts the rooms left out.

- One message in a Discord channel, posted by a webhook and edited in place,
  showing the public rooms from the rooms API: players online, each room's
  code, title, version, players, tables, open seats, the first table's
  rules, and each member's flag and character.
- Stale and unreachable states, and a stopped notice.
- A "New room opened" notice when a public room opens, deleted after 5 s so
  the channel shows as unread.
- The Docker image and compose project (`polarity-discord`), with a health check.
