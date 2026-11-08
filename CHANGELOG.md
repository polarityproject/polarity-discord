# Changelog

Releases of sfvr-discord, the Discord bot for SFVR's public rooms. Newest
first; the format follows [Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

### Added

- One message in a Discord channel, posted by a webhook and edited in place,
  showing the public rooms from the rooms API: players online, each room's
  code, title, version, players, tables, open seats, the first table's
  rules, and each member's flag and character.
- Stale and unreachable states, and a stopped notice.
- A "New room opened" notice when a public room opens, deleted after 5 s so
  the channel shows as unread.
- The Docker image and compose project (`sfvr-discord`), with a health check.
