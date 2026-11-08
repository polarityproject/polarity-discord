# sfvr-discord

A Discord bot for SFVR, the Street Fighter V rollback netplay mod: one
message in a Discord channel, kept showing the public custom rooms. It reads
the public rooms API (specified in the `sfvr-server` repository,
`docs/rooms-api.md`) like any other tool, and runs on its own in Docker,
on the rooms VPS or anywhere else.

The message: a heading with the players online, the public rooms and the
players in them, then a card per room (title, code, SFVR version, players,
tables, open seats, the first table's rules, each member's flag and
character, the owner marked). When a public room opens it also posts "New
room opened" and deletes it a few seconds later, so the channel shows as
unread.

## Layout

| Path | What it is |
| --- | --- |
| `rooms_bot.py` | the bot (Python's standard library only) |
| `test_rooms_bot.py` | its tests, against a stand-in API and webhook (no network) |
| `Dockerfile`, `compose.yaml` | the container: read-only, no capabilities, 64 MB, a health check |
| `discord.env.example` | the settings (copied to `discord.env`) |

## How it behaves

- It reads `/v1/rooms` and `/v1/summary` every 20 s (never under 10 s, the
  API's cache). It needs only outbound HTTPS and listens on nothing.
- It edits its one message when what it shows changes, and at least every
  5 minutes so the "last checked" time shows it is alive. It posts a new
  message only the first time and when someone deletes it. The message's id
  is kept in the `state` volume, so a restart or a rebuild edits the same
  message.
- When the list is stale (the rooms VPS, its keeper or publisher is down)
  the message says since when and shows no rooms; when the API cannot be
  reached for 2 minutes it says that. On `docker compose stop` it marks the
  message as not updating.
- Up to 9 rooms (Discord's 10 embeds less the heading); past that, or past
  6,000 characters, it shows fewer players a room, then "and N more rooms".
- A room's rules are its first table's (all the API carries): "Rules:" for
  a one-table room, "Table 1 rules: … (other tables may differ)" otherwise.
- **New room notice**: when a public room opens, it posts "New room opened"
  just after adding the room to the list, and deletes it 5 s later. The
  channel stays marked unread for everyone, with no pile of messages;
  members who chose "All messages" for the channel also get a notification.
  There is no notice for the rooms already open when the bot starts, for
  rooms that come back after the list was stale or unreachable, or within
  60 s of the last one (the room is still listed). A notice left behind by a
  restart is deleted when the bot starts again.
- Names are shown as plain text (markdown and mentions escaped, no pings).
- It posts through a channel **webhook**: no bot account, no gateway
  connection, nothing it can do beyond that channel. Discord shows the
  webhook's name and avatar with an APP tag. (A bot account would need a
  gateway connection before it can post at all, which needs a Discord
  library; a webhook needs nothing but HTTPS.)

## Set it up

In Discord: the channel's **Edit Channel**, **Integrations**, **Webhooks**,
**New Webhook**; name it (say "SFVR rooms") and give it an avatar before the
bot's first post (a message keeps the name it was posted with), then **Copy
Webhook URL**. Anyone with that URL can post there: keep it like a password.
Best in a channel where only the webhook posts (a read-only "rooms"
channel). For a forum post or thread, add `?thread_id=<its id>` to the URL.

On your PC, pack the repository (committed files only, so a local
`discord.env` or `secrets/` stays behind) and copy it over:

```powershell
cd D:\SteamLibrary\steamapps\common\StreetFighterV
git -C sfvr-discord archive --format=tar.gz -o ..\sfvr-discord.tgz main
scp ..\sfvr-discord.tgz polarityadmin@<vps>:~
```

On the VPS (Docker as in sfvr-server's `docs/rooms-vps-docker.md`):

```bash
sudo mkdir -p /opt/sfvr-discord
sudo tar -xzf ~/sfvr-discord.tgz -C /opt/sfvr-discord
cd /opt/sfvr-discord
sudo install -d -m 0700 secrets
sudo sh -c 'cat > secrets/discord_webhook'    # paste the URL, Enter, Ctrl+D
sudo chmod 0444 secrets/discord_webhook
sudo cp discord.env.example discord.env
sudo docker compose up -d --build
sudo docker compose logs bot
```

The log says `posted message <id>`, and the message is in the channel.
`sudo docker compose ps` shows it `healthy` after a minute.

It is its own compose project (`sfvr-discord`), apart from the rooms stack:
the rooms stack's dashboards (its `container-stats`, which lists the
`sfvr-rooms` project only) do not show it; `docker compose ps` and `logs`
here do.

## Settings

`discord.env` (all optional; after a change, `sudo docker compose up -d`):

| Setting | Default | What it does |
| --- | --- | --- |
| `ROOMS_API_URL` | the public Worker | the rooms API's address, without a path |
| `POLL_EVERY` | 20 | seconds between reads (at least 10) |
| `REFRESH_EVERY` | 300 | the longest without an edit (the "last checked" time) |
| `FAIL_AFTER` | 120 | seconds of API failures before the message says it cannot be reached |
| `HEADING` | SFVR public rooms | the message's heading |
| `MAX_ROOMS` | 9 | the most room cards (at most 9) |
| `ROOM_VERSIONS` | every version | SFVR versions listed, comma-separated (`0.3.0`) |
| `NEW_ROOM_NOTICE` | 1 | 0: no new room notices |
| `NEW_ROOM_TEXT` | New room opened | the notice's text |
| `NEW_ROOM_DELETE_AFTER` | 5 | seconds before the notice is deleted |
| `NEW_ROOM_COOLDOWN` | 60 | the shortest gap between two notices |
| `MESSAGE_ID` | | adopt a message the same webhook posted before |
| `OFFLINE_NOTICE` | 1 | on stopping, say the list is not updating |

## Changing it

| What | How |
| --- | --- |
| Another channel | a webhook there, its URL in `secrets/discord_webhook`, `sudo docker compose up -d --force-recreate`: it posts a new message there (the old one stays; delete it by hand) |
| A new webhook URL, same channel | the same; the new webhook cannot edit the old one's message, so it posts a new one |
| A message deleted | nothing: it posts a new one at its next edit (within 5 minutes) |
| A new version | pack and copy as above, untar over `/opt/sfvr-discord`, `sudo docker compose up -d --build` |
| Image security fixes | `sudo docker compose build --pull && sudo docker compose up -d` |
| Stop it | `sudo docker compose stop` (the message says it is stopped); `sudo docker compose down` removes the container, `down -v` the saved message id too |

## Testing

From any PC with Python 3.10 or later:

```bash
python test_rooms_bot.py          # the tests (no network)
python rooms_bot.py --print       # the live list rendered as the message's JSON; no Discord
```

`rooms_bot.py --once` does one update and exits (with `DISCORD_WEBHOOK_URL`
set, or `DISCORD_SECRET_FILE` naming a file with the URL).
