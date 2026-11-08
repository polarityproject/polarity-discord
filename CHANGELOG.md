# Changelog

Releases of sfvr-discord, the Discord bot for SFVR's public rooms. Newest
first; the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

### Added

- Only the latest release's rooms are listed by default (`ROOM_VERSIONS`
  `latest`): the game lists only its own version's rooms, so others could not
  be found. The latest release is read from the SFVR site's update info every
  5 minutes; the heading names it and counts the rooms left out.

- One message in a Discord channel, posted by a webhook and edited in place,
  showing the public rooms from the rooms API: players online, each room's
  code, title, version, players, tables, open seats, the first table's
  rules, and each member's flag and character.
- Stale and unreachable states, and a stopped notice.
- A "New room opened" notice when a public room opens, deleted after 5 s so
  the channel shows as unread.
- The Docker image and compose project (`sfvr-discord`), with a health check.
