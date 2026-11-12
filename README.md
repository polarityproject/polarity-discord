# polarity-discord

A Discord bot for Polarity, the Street Fighter V rollback netplay mod: one
message in a Discord channel, kept showing the public custom rooms. It reads
the public rooms API (specified in the `polarity-server` repository,
`docs/rooms-api.md`) like any other tool, and runs on its own in Docker,
on the rooms VPS or anywhere else.

The message: a heading with the players online, the public rooms and the
players in them, then a card per room (title, code, Polarity version, players,
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
- It edits its one message at every look, changed or not, so the "last
  checked" time shows it is polling (one edit every 20 s, far under
  Discord's webhook limits; `REFRESH_EVERY` spaces the unchanged edits out).
  While the API fails it keeps the last list unedited, so "last checked"
  stops for up to 2 minutes before the message says so. It posts a new
  message only the first time and when someone deletes it. The message's id
  is kept in the `state` volume, so a restart or a rebuild edits the same
  message.
- When the list is stale (the rooms VPS, its keeper or publisher is down)
  the message says since when and shows no rooms; when the API cannot be
  reached for 2 minutes it says that. On `docker compose stop` it marks the
  message as not updating.
- **Only the latest version's rooms.** The game lists only rooms of its own
  Polarity version (the exact string, so 0.3.0 and 0.3.0.1 do not mix) and leaves
  another version's room as soon as it joins one by code. So by default the
  bot lists only the rooms of the latest release, which it reads from the
  update info the mod's updater uses (`https://polaritymod.link/update.json`,
  its `version`) every 5 minutes. The heading says which version, and how
  many rooms on other versions were left out; the totals count only listed
  rooms (the players online stay everyone's). A release shows up within
  5 minutes of being on the site, and its rooms then count as new (a notice).
  If the site cannot be read, it keeps the last version it read, or lists
  every version (each card naming its own) until it first can.
  `ROOM_VERSIONS` changes this.
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
**New Webhook**; name it (say "Polarity rooms") and give it an avatar before the
bot's first post (a message keeps the name it was posted with), then **Copy
Webhook URL**. Anyone with that URL can post there: keep it like a password.
Best in a channel where only the webhook posts (a read-only "rooms"
channel). For a forum post or thread, add `?thread_id=<its id>` to the URL.

On your PC, pack the repository (committed files only, so a local
`discord.env` or `secrets/` stays behind) and copy it over:

```powershell
cd D:\SteamLibrary\steamapps\common\StreetFighterV
git -C polarity-discord archive --format=tar.gz -o ..\polarity-discord.tgz main
scp ..\polarity-discord.tgz polarityadmin@<vps>:~
```

On the VPS (Docker as in polarity-server's `docs/rooms-vps-docker.md`):

```bash
sudo mkdir -p /opt/polarity-discord
sudo tar -xzf ~/polarity-discord.tgz -C /opt/polarity-discord
cd /opt/polarity-discord
sudo install -d -m 0700 secrets
sudo sh -c 'cat > secrets/discord_webhook'    # paste the URL, Enter, Ctrl+D
sudo chmod 0444 secrets/discord_webhook
sudo cp discord.env.example discord.env
sudo docker compose up -d --build
sudo docker compose logs bot
```

The log says `posted message <id>`, and the message is in the channel.
`sudo docker compose ps` shows it `healthy` after a minute.

It is its own compose project (`polarity-discord`), apart from the rooms stack:
the rooms stack's dashboards (its `container-stats`, which lists the
`sfvr-rooms` project only) do not show it; `docker compose ps` and `logs`
here do.

## Settings

`discord.env` (all optional; after a change, `sudo docker compose up -d`):

| Setting | Default | What it does |
| --- | --- | --- |
| `ROOMS_API_URL` | `https://api.polaritymod.link` | the rooms API's address, without a path |
| `POLL_EVERY` | 20 | seconds between reads (at least 10) |
| `REFRESH_EVERY` | 20 | the longest without an edit while nothing changes (the "last checked" time; at least 10; at or under `POLL_EVERY`: every look) |
| `FAIL_AFTER` | 120 | seconds of API failures before the message says it cannot be reached |
| `HEADING` | Polarity public rooms | the message's heading |
| `MAX_ROOMS` | 9 | the most room cards (at most 9) |
| `ROOM_VERSIONS` | latest | whose rooms are listed: `latest`, `all`, or versions, comma-separated (`latest,0.3.0`) |
| `LATEST_URL` | `https://polaritymod.link/update.json` | the update info the latest release is read from (its `version`) |
| `LATEST_EVERY` | 300 | seconds between reads of it (at least 60) |
| `NEW_ROOM_NOTICE` | 1 | 0: no new room notices |
| `NEW_ROOM_TEXT` | New room opened | the notice's text |
| `NEW_ROOM_DELETE_AFTER` | 5 | seconds before the notice is deleted |
| `NEW_ROOM_COOLDOWN` | 60 | the shortest gap between two notices |
| `MESSAGE_ID` | | adopt a message the same webhook posted before |
| `OFFLINE_NOTICE` | 1 | on stopping, say the list is not updating |
| `TRACE` | 0 | 1: a log line for every look (see "When updates seem slow") |

## When updates seem slow

How long a change normally takes to reach the message: the keeper writes the
list every 5 s, the publisher pushes a changed list within 10 s, the Worker's
cache holds it up to 10 s, and the bot looks every 20 s. So a room should
show up within about 45 s. The "last checked" time moves at every look, so
one older than about 40 s means the bot is not getting through (the trace
below says why).

Always logged: any call taking 5 s or more (`slow call: PATCH message took
7.2 s (200)`), any look taking 10 s or more (with its calls), and the rooms
list going more than 55 s without moving (`the rooms list is 70 s old … the
rooms server's keeper or publisher is behind`), then catching up again.

For every look, set `TRACE=1` in `discord.env`, `sudo docker compose up -d`,
and read `sudo docker compose logs -f -t bot`:

```
look: live, 2 rooms, edited in 0.74 s; list 12 s old (age 12); GET rooms 0.53 s, GET summary 0.21 s, PATCH message 0.30 s
look (14 s late): live, 2 rooms, edited (refresh) in 0.77 s; list 33 s old (age 31); GET rooms 0.21 s, GET summary 0.25 s, PATCH message 0.31 s
```

| What you see | Where the time went |
| --- | --- |
| Looks more than 20 s apart, `(N s late)`, or a slow call | the bot or its network (DNS, Discord, Cloudflare); the slow call names which |
| `list N s old` climbing past ~50 s while rooms change in the game | upstream: the rooms server's keeper or publisher (their logs on the VPS) |
| `list N s old` more than 10 s over `age` | a cached answer held too long, or the bot's clock is off |
| `edited` on time but Discord shows the old message | Discord's client: Ctrl+R in Discord shows the message as it is |
| `Discord rate limit, waiting` | Discord slowing the webhook down (another tool on the same webhook?) |

The `-t` adds Docker's own timestamps; compare them with the bot's to see a
log line written late.

## Changing it

| What | How |
| --- | --- |
| Another channel | a webhook there, its URL in `secrets/discord_webhook`, `sudo docker compose up -d --force-recreate`: it posts a new message there (the old one stays; delete it by hand) |
| A new webhook URL, same channel | the same; the new webhook cannot edit the old one's message, so it posts a new one |
| A message deleted | nothing: it posts a new one at its next look |
| A new version | pack and copy as above, untar over `/opt/polarity-discord`, `sudo docker compose up -d --build` |
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
