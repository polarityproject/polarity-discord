#!/usr/bin/env python3
# polarity-discord: keeps one message in a Discord channel showing Polarity's public
# custom rooms, read from the rooms API (polarity-server's docs/rooms-api.md). It edits
# that one message in place; it never posts a second one unless the first is
# deleted.
#
# Every POLL_EVERY seconds (20; never under 10, the API's cache): GET
# <ROOMS_API_URL>/v1/rooms and /v1/summary, render the message, and edit it
# when what it shows changed, or at least every REFRESH_EVERY seconds (20:
# every look) so its "checked" time shows the bot is polling.
#
# It posts through a channel webhook (https://discord.com/api/webhooks/<id>/
# <token>, from the channel's Integrations settings, in the secret file):
# no bot account, no gateway connection, no permissions beyond that one
# channel; Discord shows the webhook's name and avatar with an APP tag. A
# webhook can edit only its own messages. Forum or thread channels: add
# ?thread_id=<id> to the URL.
# The message's id is kept in STATE_DIR/state.json; MESSAGE_ID adopts an
# existing message instead (one this webhook posted).
#
# It lists only the rooms of the latest Polarity release by default (ROOM_VERSIONS
# "latest"): the game lists only its own version's rooms and leaves another
# version's room at once. The latest release is the "version" of the update
# info the mod's updater reads (LATEST_URL, the Polarity site's update.json),
# read every LATEST_EVERY seconds (300).
#
# When a public room opens it also posts a short notice ("New room opened")
# and deletes it NEW_ROOM_DELETE_AFTER seconds later (5): the channel shows
# as unread, without a pile of messages. None for the rooms already open
# when it starts, nor after the list was stale or unreachable; at most one
# every NEW_ROOM_COOLDOWN seconds (60).
#
# Standard library only. `rooms_bot.py --print` renders the live list to
# stdout without touching Discord; `--once` does one update and exits.
import argparse
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1"
USER_AGENT = f"DiscordBot (https://polaritymod.link, {VERSION}) polarity-rooms-discord"

# Discord's limits on one message (embeds).
MAX_EMBEDS = 10
MAX_TOTAL = 6000
MAX_DESC = 4096
MAX_TITLE = 256

COLOR_LIVE = 0x3BA55C      # header when live; a room with open seats
COLOR_JOINABLE = 0xFAA61A  # seats taken, room to join (queue or watch)
COLOR_FULL = 0x747F8D      # room full; anything not live
COLOR_DOWN = 0xED4245      # header when the list is stale or unreachable

CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")

# docs/rooms-api.md, "Character codes".
CHARACTERS = {
    "RYU": "Ryu", "KEN": "Ken", "DSM": "Dhalsim", "JRI": "Juri", "RSD": "Rashid",
    "NCL": "Necalli", "IBK": "Ibuki", "ALX": "Alex", "Z22": "Ed", "Z25": "Zeku",
    "Z28": "Falke", "Z31": "Sagat", "Z34": "E. Honda", "Z37": "Seth", "Z40": "Oro",
    "VEG": "M. Bison", "NSH": "Nash", "BRD": "Birdie", "FAN": "F.A.N.G", "CMY": "Cammy",
    "BSN": "Balrog", "Z21": "Akuma", "Z23": "Menat", "Z26": "Sakura", "Z29": "Cody",
    "Z32": "Kage", "Z35": "Lucia", "Z38": "Dan", "Z41": "Akira", "ELV": "Eleven",
    "CNL": "Chun-Li", "KRN": "Karin", "ZGF": "Zangief", "BLR": "Vega", "RMK": "R. Mika",
    "LAR": "Laura", "GUL": "Guile", "URN": "Urien", "Z20": "Kolin", "Z24": "Abigail",
    "Z27": "Blanka", "Z30": "G", "Z33": "Poison", "Z36": "Gill", "Z39": "Rose",
    "Z42": "Luke",
}


def log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + text, flush=True)


# Diagnostics: every HTTP call is timed. A call this slow is always logged, as
# is a look this slow (with its calls), and a rooms list this old.
SLOW_CALL = 5.0
SLOW_LOOK = 10.0
BEHIND = 55.0  # the publisher heartbeats every 30 s, so up to ~50 s is normal for an unchanged list
CALLS = []  # (label, seconds, outcome) since the current look began


def call_label(method, url):
    """A call's name for the log (never the webhook token)."""
    path = urllib.parse.urlsplit(url).path
    m = re.search(r"/webhooks/\d+/[^/]+(/.*)?$", path)
    if m:
        return method + (" message" if (m.group(1) or "").startswith("/messages/") else " webhook")
    return method + " " + path.rsplit("/", 1)[-1]


def env_float(name, default, low):
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(low, value)


# ---------------------------------------------------------------- rendering

def escape(text):
    """Player-chosen text as plain text in Discord markdown."""
    text = str(text).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\*_~`|>#<\[\]()@:-])", r"\\\1", text)


def character(code):
    if not code:
        return None
    return CHARACTERS.get(code, str(code))


def flag(country):
    if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country):
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country)
    return None


def describe_rules(rules):
    """The game's own wording: "First to 2 · winner stays, up to 3 · 99 seconds"."""
    if not isinstance(rules, str) or not rules.strip():
        return None
    tokens = rules.split()
    if "r1" not in tokens:
        return escape(rules)
    first, rotation, timer, rounds, other = None, None, None, None, []
    for token in tokens:
        m = re.fullmatch(r"(ft|bo|t|ws|ls)(\d+)", token)
        if token == "r1":
            continue
        if token == "rr":
            rotation = "round robin"
        elif m and m.group(1) == "ft":
            first = f"First to {m.group(2)}"
        elif m and m.group(1) == "bo":
            n = int(m.group(2))
            rounds = None if n == 3 else ("one round" if n == 1 else f"best of {n}")
        elif m and m.group(1) == "t":
            timer = f"{m.group(2)} seconds"
        elif m and m.group(1) in ("ws", "ls"):
            who = "winner" if m.group(1) == "ws" else "loser"
            n = int(m.group(2))
            rotation = f"{who} stays" + (f", up to {n}" if n else "")
        else:
            other.append(escape(token))
    parts = [p for p in (first, rotation, timer, rounds) if p] + other
    return " · ".join(parts) if parts else None


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def seat_label(player):
    """A seated player: flag, name, character; None (an open seat): italic."""
    if not isinstance(player, dict) or not player.get("name"):
        return "*open seat*"
    parts = []
    f = flag(player.get("country"))
    if f:
        parts.append(f)
    parts.append(escape(player["name"]))
    chara = character(player.get("face"))
    if chara:
        parts.append(f"({escape(chara)})")
    return " ".join(parts)


TABLE_STATES = {"called": "getting ready", "connecting": "starting", "playing": "playing", "after": "set over"}


def table_lines(table, detailed):
    """One table of the API's table_list: its number and rules, then its two
    seats with what it is doing (detailed); else both on one line."""
    if not isinstance(table, dict):
        return []
    number = table.get("table") or "?"
    seats = table.get("seats") if isinstance(table.get("seats"), list) else [None, None]
    seats = (seats + [None, None])[:2]
    open_both = not any(isinstance(p, dict) and p.get("name") for p in seats)
    state = table.get("state")
    who = "Open" if open_both else f"{seat_label(seats[0])} vs {seat_label(seats[1])}"
    doing = TABLE_STATES.get(state)
    if state == "playing":
        score = table.get("score") if isinstance(table.get("score"), list) else []
        if len(score) == 2 and all(isinstance(n, int) for n in score):
            doing = f"playing, {score[0]}–{score[1]}"
    if doing and not open_both:
        who += f" · {doing}"
    if not detailed:
        return [f"**Table {number}:** {who}"]
    rules = describe_rules(table.get("rules"))
    return [f"**Table {number}**" + (f" · {rules}" if rules else ""), who]


def room_embed(room, player_cap, show_version=True):
    code = str(room.get("code", ""))
    code_text = f"`{code}`" if CODE_RE.match(code) else escape(code)
    owner = room.get("owner") or ""
    title = room.get("title") or (f"{owner}'s room" if owner else "A room")
    members = int(room.get("members") or 0)
    capacity = int(room.get("capacity") or 16)
    tables = int(room.get("tables") or 1)
    seats = int(room.get("open_seats") or 0)

    if seats > 0:
        color, seat_text = COLOR_LIVE, plural(seats, "open seat")
    elif members < capacity:
        color, seat_text = COLOR_JOINABLE, "seats taken, room to queue or watch"
    else:
        color, seat_text = COLOR_FULL, "full"

    version = room_version(room)
    lines = [
        f"**Code {code_text}**" + (f" · Polarity {escape(version)}" if version and show_version else ""),
        f"{members}/{capacity} players · {plural(tables, 'table')} · {seat_text}",
    ]
    players = room.get("players") or []
    table_list = room.get("table_list")
    if isinstance(table_list, list) and table_list:
        # Every table with who sits there (keepers from 0.4.0); the players
        # list then names only those not seated.
        lines.append("")
        for table in table_list:
            lines.extend(table_lines(table, player_cap > 0))
        seated = []
        for table in table_list:
            for p in (table.get("seats") or []) if isinstance(table, dict) else []:
                if isinstance(p, dict) and p.get("name"):
                    seated.append(p["name"])
        rest = []
        for p in players:
            if p.get("name") in seated:
                seated.remove(p.get("name"))  # one seat per name (names are not unique)
            else:
                rest.append(p)
        players = rest
        if players and player_cap > 0:
            lines.append("")
            lines.append("**Also in the room**")
    else:
        # A keeper before 0.4.0: the first table's rules only.
        rules = describe_rules(room.get("rules"))
        if rules:
            lines.append(f"Rules: {rules}" if tables == 1 else f"Table 1 rules: {rules} *(other tables may differ)*")

    if player_cap > 0 and players:
        if not (isinstance(table_list, list) and table_list):
            lines.append("")
        owner_marked = False
        for p in players[:player_cap]:
            name = p.get("name") or "?"
            parts = []
            f = flag(p.get("country"))
            if f:
                parts.append(f)
            entry = escape(name)
            if not owner_marked and owner and name == owner:
                entry = f"**{entry}** 👑"
                owner_marked = True
            parts.append(entry)
            chara = character(p.get("face"))
            if chara:
                parts.append(f"— {escape(chara)}")
            lines.append(" ".join(parts))
        if len(players) > player_cap:
            lines.append(f"*and {len(players) - player_cap} more*")
    elif players:
        faces = [character(c) for c in (room.get("faces") or []) if c]
        if faces:
            lines.append(", ".join(escape(c) for c in faces))

    return {
        "title": escape(title)[:MAX_TITLE],
        "description": "\n".join(lines)[:MAX_DESC],
        "color": color,
    }


def embed_size(embed):
    size = len(embed.get("title", "")) + len(embed.get("description", ""))
    size += len(embed.get("footer", {}).get("text", ""))
    return size


def room_version(room):
    """The Polarity version a room plays: client_version, else sfvr_version
    (its name from keepers before 0.4.0)."""
    return room.get("client_version") or room.get("sfvr_version")


def all_rooms(rooms_doc):
    if rooms_doc and isinstance(rooms_doc.get("rooms"), list):
        return [r for r in rooms_doc["rooms"] if isinstance(r, dict)]
    return []


def listed_rooms(rooms_doc, settings):
    """The rooms the message lists: those of settings["shown"] (a set of
    versions; None: every version). The game lists only its own version's
    rooms and leaves another version's room at once, so by default that is
    the latest release's."""
    shown = settings.get("shown")
    rooms = all_rooms(rooms_doc)
    if shown is not None:
        rooms = [r for r in rooms if room_version(r) in shown]
    return rooms


def resolve_versions(settings, latest):
    """settings["versions"] (ROOM_VERSIONS: "latest", "all", or versions) as
    settings["shown"], with `latest` the latest release (None: unknown, then
    every version is listed rather than none)."""
    tokens = settings["versions"]
    settings["latest"] = latest
    if not tokens or "all" in tokens:
        settings["shown"] = None
        return
    shown = {latest if t == "latest" else t for t in tokens}
    shown.discard(None)
    settings["shown"] = shown or None


def render(rooms_doc, summary_doc, state, settings, checked):
    """The message: {"content", "embeds"}. `state` is "live", "stale",
    "unreachable" or "stopped"; `checked` is the Unix time shown as checked."""
    heading = settings["heading"]
    poll = settings["poll"]
    header = {"title": escape(heading)[:MAX_TITLE]}
    lines = []
    rooms = listed_rooms(rooms_doc, settings)
    totals = (summary_doc or {}).get("totals") or {}
    updated = int(((rooms_doc or {}).get("updated") or 0) / 1000)

    if state == "live":
        header["color"] = COLOR_LIVE
        online = totals.get("online")
        line = []
        if isinstance(online, int):
            line.append(f"**{online}** online")
        line.append(f"**{len(rooms)}** public {'room' if len(rooms) == 1 else 'rooms'}")
        in_rooms = sum(int(r.get("members") or 0) for r in rooms)
        line.append(f"**{in_rooms}** in them")
        lines.append("🟢 " + " · ".join(line))
        shown = settings.get("shown")
        if shown:
            hidden = len(all_rooms(rooms_doc)) - len(rooms)
            latest = settings.get("latest")
            if shown == {latest}:
                text = f"Rooms on Polarity **{escape(latest)}**, the latest version"
            else:
                text = "Rooms on Polarity " + ", ".join(f"**{escape(v)}**" for v in sorted(shown))
            if hidden > 0:
                text += f" ({plural(hidden, 'room')} on other versions not listed)"
            lines.append(text + ".")
        if rooms:
            lines.append("Join in the game: **Custom Rooms → Join with a code**.")
        else:
            lines.append("No public rooms open right now. Make one in the game: "
                         "**Custom Rooms → Create a room**, Visibility **Public**.")
    else:
        header["color"] = COLOR_DOWN
        rooms = []
        if state == "stale":
            since = f" since <t:{updated}:R>" if updated else ""
            lines.append(f"🔴 The rooms server has not reported{since}. "
                         "Rooms will show here again when it is back.")
        elif state == "unreachable":
            lines.append(f"⚠️ The rooms list cannot be reached since <t:{settings['failing_since']}:R>.")
        else:
            lines.append("⏸️ This list is not updating: the bot is stopped.")
    if checked:
        if state == "stopped":
            lines.append(f"Stopped <t:{int(checked)}:R>")
        else:
            lines.append(f"Checks every {poll:g} s · last checked <t:{int(checked)}:R>")
    header["description"] = "\n".join(lines)

    # Rooms, most players shown that still fit in one message.
    limit = min(settings["max_rooms"], MAX_EMBEDS - 1)
    picked = rooms[:limit]
    more = len(rooms) - len(picked)
    # (with room for the "and N more" line).
    def fits(embeds):
        return embed_size(header) + sum(embed_size(e) for e in embeds) <= MAX_TOTAL - 100

    # A card names its version only when the list holds more than one.
    shown = settings.get("shown")
    show_versions = not (shown and len(shown) == 1)
    for cap in (16, 8, 4, 0):
        embeds = [room_embed(r, cap, show_versions) for r in picked]
        if fits(embeds):
            break
    while embeds and not fits(embeds):
        embeds.pop()
    more += len(picked) - len(embeds)
    if more > 0:
        header["description"] += f"\n*and {plural(more, 'more room')}, in the game's list*"
    return {"content": "", "embeds": [header] + embeds, "allowed_mentions": {"parse": []}}


def strip_checked(payload):
    """The payload without its checked time: what decides whether to edit."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return re.sub(r"last checked <t:\d+:R>", "", text)


# ---------------------------------------------------------------- HTTP

class HttpError(Exception):
    def __init__(self, status, body, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after = retry_after

    def discord_code(self):
        try:
            return json.loads(self.body).get("code")
        except (ValueError, AttributeError):
            return None


def request(method, url, headers=None, body=None, timeout=15):
    data = None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    start, outcome = time.monotonic(), "error"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(4 * 1024 * 1024)
            outcome = str(r.status)
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        outcome = str(e.code)
        raw = e.read(64 * 1024).decode("utf-8", "replace")
        e.close()
        retry = None
        if e.code == 429:
            try:
                retry = float(json.loads(raw).get("retry_after"))
            except (ValueError, TypeError, AttributeError):
                retry = float(e.headers.get("Retry-After") or 5)
        raise HttpError(e.code, raw, retry) from None
    except OSError as e:
        outcome = type(e).__name__
        raise
    finally:
        took = time.monotonic() - start
        label = call_label(method, url)
        CALLS.append((label, took, outcome))
        if took >= SLOW_CALL:
            log(f"rooms-bot: slow call: {label} took {took:.1f} s ({outcome})")


class RoomsApi:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def get(self, path):
        _, doc = request("GET", self.base + path, timeout=15)
        if not isinstance(doc, dict) or doc.get("version") != 1:
            raise ValueError(f"{path}: not a version 1 answer")
        return doc


VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


class Latest:
    """The latest Polarity release, from the update info the mod's updater reads
    (the Polarity site's update.json, its "version"). Read every `every` seconds;
    the last one read is kept while the site does not answer."""

    def __init__(self, url, every):
        self.url = url
        self.every = every
        self.version = None
        self.next = 0.0
        self.said_failing = False

    def get(self, now):
        if not self.url or now < self.next:
            return self.version
        self.next = now + self.every
        try:
            _, doc = request("GET", self.url, timeout=15)
            version = doc.get("version") if isinstance(doc, dict) and doc.get("product") in ("Polarity", "SFVR") else None
            if not isinstance(version, str) or not VERSION_RE.match(version):
                raise ValueError("no Polarity version in it")
        except (HttpError, ValueError, OSError) as e:
            if not self.said_failing:
                keep = f"keeping {self.version}" if self.version else "listing every version until it answers"
                log(f"rooms-bot: cannot read the latest version from {self.url} ({e}); {keep}")
                self.said_failing = True
            if self.version is None:
                self.next = now + min(self.every, 60)
            return self.version
        if version != self.version:
            log(f"rooms-bot: latest Polarity version {version}" + (f" (was {self.version})" if self.version else ""))
        self.version, self.said_failing = version, False
        return self.version


class Discord:
    """One message, through a channel webhook."""

    def __init__(self, url):
        parsed = urllib.parse.urlsplit(url.strip())
        parts = parsed.path.rstrip("/").split("/")
        if parsed.scheme not in ("http", "https") or len(parts) < 3 or parts[-3] != "webhooks":
            raise SystemExit("rooms-bot: the secret is not a webhook URL (https://discord.com/api/webhooks/<id>/<token>)")
        self.base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.thread = (urllib.parse.parse_qs(parsed.query).get("thread_id") or [None])[0]
        # Which webhook the saved message belongs to (its id, never the token).
        self.target = "webhook:" + parts[-2] + (":" + self.thread if self.thread else "")

    def _url(self, path, wait=False):
        query = {}
        if wait:
            query["wait"] = "true"
        if self.thread:
            query["thread_id"] = self.thread
        return self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")

    def _call(self, method, path, body=None, wait=False):
        for attempt in range(3):
            try:
                return request(method, self._url(path, wait), None, body)[1]
            except HttpError as e:
                if e.status == 429 and attempt < 2:
                    delay = min(max(e.retry_after or 1.0, 0.5), 60.0)
                    log(f"rooms-bot: Discord rate limit, waiting {delay:.1f} s")
                    time.sleep(delay)
                    continue
                raise

    def post(self, payload):
        return str(self._call("POST", "", payload, wait=True)["id"])

    def edit(self, message_id, payload):
        self._call("PATCH", f"/messages/{message_id}", payload)

    def delete(self, message_id):
        self._call("DELETE", f"/messages/{message_id}")


# ---------------------------------------------------------------- state

class State:
    def __init__(self, folder):
        self.path = os.path.join(folder, "state.json") if folder else None
        self.health = os.path.join(folder, "healthy") if folder else None

    def load(self):
        if not self.path:
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, doc):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, self.path)

    def alive(self):
        if not self.health:
            return
        try:
            with open(self.health, "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass


# ---------------------------------------------------------------- loop

class Stop(Exception):
    pass


def on_signal(signum, frame):
    raise Stop()


class Bot:
    def __init__(self, api, discord, state, settings):
        self.api = api
        self.discord = discord
        self.state = state
        self.settings = settings
        saved = state.load()
        same = saved.get("target") == discord.target
        self.message_id = settings["message_id"] or (saved.get("message_id") if same else None)
        # New-room notices still to delete: [{"id", "due", "tries"}]. Ones left
        # from before a restart go at the first sweep.
        self.notices = [{"id": str(n["id"]), "due": 0, "tries": 0}
                        for n in (saved.get("notices") or []) if same and isinstance(n, dict) and n.get("id")]
        self.known = None  # the listed rooms' codes at the last look; None: no baseline yet
        self.last_notice = 0.0
        self.latest = Latest(settings["latest_url"], settings["latest_every"])
        self.last_key = None
        self.last_edit = 0.0
        self.rooms_doc = None
        self.summary_doc = None
        self.failing_since = None
        self.said_failing = False
        self.discord_failing = False
        self.behind_since = None

    def list_age(self):
        """Seconds since the rooms server's keeper wrote the list we have."""
        if not self.rooms_doc or not self.rooms_doc.get("updated"):
            return None
        return time.time() - self.rooms_doc["updated"] / 1000

    def watch_age(self):
        """Log when the list falls behind (the keeper or publisher not
        pushing) and when it catches up."""
        lag = self.list_age()
        if lag is None:
            return
        if lag > BEHIND and self.behind_since is None:
            self.behind_since = time.time() - lag
            log(f"rooms-bot: the rooms list is {lag:.0f} s old (age {self.rooms_doc.get('age')}): "
                "the rooms server's keeper or publisher is behind")
        elif lag <= BEHIND and self.behind_since is not None:
            log(f"rooms-bot: the rooms list is fresh again ({lag:.0f} s old; "
                f"it had not moved for {time.time() - lag - self.behind_since:.0f} s)")
            self.behind_since = None

    def fetch(self):
        """Fetch both documents; return the state to show."""
        try:
            rooms = self.api.get("/v1/rooms")
            try:
                summary = self.api.get("/v1/summary")
            except (HttpError, ValueError, OSError):
                summary = self.summary_doc  # the totals line can lag one look
            self.rooms_doc, self.summary_doc = rooms, summary
            if self.failing_since is not None:
                log(f"rooms-bot: rooms API answering again after {time.time() - self.failing_since:.0f} s")
            self.failing_since, self.said_failing = None, False
            self.watch_age()
        except (HttpError, ValueError, OSError) as e:
            now = time.time()
            if self.failing_since is None:
                self.failing_since = now
            if not self.said_failing:
                log(f"rooms-bot: rooms API failed ({e}); trying every {self.settings['poll']:g} s")
                self.said_failing = True
            if now - self.failing_since < self.settings["fail_after"] and self.rooms_doc:
                return None  # keep showing what is there for a short outage
            self.settings["failing_since"] = int(self.failing_since)
            return "unreachable"
        if self.rooms_doc.get("stale"):
            return "stale"
        # The API's age can lag its cache by 10 s; check `updated` too.
        if time.time() - self.rooms_doc.get("updated", 0) / 1000 > 120:
            return "stale"
        return "live"

    def publish(self, payload):
        if self.message_id:
            try:
                self.discord.edit(self.message_id, payload)
                return "edited"
            except HttpError as e:
                # 10008 Unknown Message: deleted; post a new one.
                if e.status == 404 and e.discord_code() == 10008:
                    log(f"rooms-bot: message {self.message_id} is gone; posting a new one")
                    self.message_id = None
                else:
                    raise
        self.message_id = self.discord.post(payload)
        self.save()
        log(f"rooms-bot: posted message {self.message_id}")
        return "posted"

    def save(self):
        try:
            self.state.save({"target": self.discord.target, "message_id": self.message_id,
                             "notices": [{"id": n["id"]} for n in self.notices]})
        except OSError as e:
            log(f"rooms-bot: cannot save the state ({e})")

    def check_new_rooms(self, shown, now):
        """Post a notice when a room opened since the last look."""
        if shown != "live":
            self.known = None  # no notices for what reappears after an outage
            return
        codes = {str(r.get("code")) for r in listed_rooms(self.rooms_doc, self.settings)}
        new = codes - self.known if self.known is not None else set()
        self.known = codes
        if not new or not self.settings["notify"]:
            return
        names = ", ".join(sorted(new))
        if now - self.last_notice < self.settings["notify_cooldown"]:
            log(f"rooms-bot: new room {names}; no notice (the last was {now - self.last_notice:.0f} s ago)")
            return
        try:
            mid = self.discord.post({"content": self.settings["notify_text"], "allowed_mentions": {"parse": []}})
        except (HttpError, OSError) as e:
            log(f"rooms-bot: new room {names}; the notice failed ({e})")
            return
        self.last_notice = now
        self.notices.append({"id": mid, "due": now + self.settings["notify_delete"], "tries": 0})
        self.save()
        log(f"rooms-bot: new room {names}; notice {mid} posted")

    def sweep(self, now, everything=False):
        """Delete the notices whose time is up (all of them, when stopping)."""
        if not self.notices:
            return
        kept = []
        for n in self.notices:
            if not everything and n["due"] > now:
                kept.append(n)
                continue
            try:
                self.discord.delete(n["id"])
            except (HttpError, OSError) as e:
                if not (isinstance(e, HttpError) and e.status == 404):  # 404: already gone
                    n["tries"] += 1
                    if n["tries"] < 5 and not everything:
                        n["due"] = now + 5 * n["tries"]
                        kept.append(n)
                    else:
                        log(f"rooms-bot: could not delete notice {n['id']} ({e}); leaving it")
        changed = len(kept) != len(self.notices)
        self.notices = kept
        if changed:
            self.save()

    def step(self, force=False, late=0.0):
        """One look: read the API, edit the message if it changed. Traced
        (TRACE=1), or logged anyway when it was slow."""
        CALLS.clear()
        start = time.monotonic()
        outcome = self._step(force)
        took = time.monotonic() - start
        if not (self.settings["trace"] or took >= SLOW_LOOK):
            return
        line = f"rooms-bot: look{f' ({late:.0f} s late)' if late >= 1 else ''}: {outcome} in {took:.2f} s"
        lag = self.list_age()
        if lag is not None:
            line += f"; list {lag:.0f} s old (age {self.rooms_doc.get('age')})"
        calls = ", ".join(f"{label} {secs:.2f} s" + ("" if out.startswith("2") else f" ({out})")
                          for label, secs, out in CALLS)
        log(line + (f"; {calls}" if calls else ""))

    def _step(self, force):
        if "latest" in self.settings["versions"]:
            resolve_versions(self.settings, self.latest.get(time.time()))
        else:
            resolve_versions(self.settings, None)
        shown = self.fetch()
        if shown is None:
            self.state.alive()
            return "API failing, kept the last list"
        now = time.time()
        payload = render(self.rooms_doc, self.summary_doc, shown, self.settings, now)
        key = strip_checked(payload)
        # Half a look's slack, so a refresh as long as the look edits at every
        # look however long the calls took.
        due = now - self.last_edit >= self.settings["refresh"] - self.settings["poll"] / 2
        rooms = len(listed_rooms(self.rooms_doc, self.settings))
        what = f"{shown}, {plural(rooms, 'room')}"
        if key == self.last_key and not due and not force:
            self.check_new_rooms(shown, now)
            self.state.alive()
            return what + ", no change"
        try:
            done = self.publish(payload)
        except (HttpError, OSError) as e:
            detail = ""
            if isinstance(e, HttpError):
                detail = f": {e.body[:200]}"
                if e.status in (401, 403):
                    detail += " (check the webhook URL)"
            if not self.discord_failing:
                log(f"rooms-bot: Discord update failed ({e}{detail})")
                self.discord_failing = True
            return what + ", Discord failed"
        if self.discord_failing:
            log("rooms-bot: Discord updates working again")
            self.discord_failing = False
        changed = key != self.last_key
        if changed:
            log(f"rooms-bot: message updated ({what})")
        self.last_key, self.last_edit = key, now
        # After the edit, so whoever the notice brings in finds the room listed.
        self.check_new_rooms(shown, now)
        self.state.alive()
        return f"{what}, {done}" + ("" if changed else " (refresh)")

    def stopped(self):
        self.sweep(time.time(), everything=True)
        if not self.message_id or not self.settings["offline_notice"]:
            return
        payload = render(self.rooms_doc, self.summary_doc, "stopped", self.settings, time.time())
        try:
            self.discord.edit(self.message_id, payload)
            log("rooms-bot: marked the message as stopped")
        except (HttpError, OSError) as e:
            log(f"rooms-bot: could not mark the message as stopped ({e})")


def load_settings():
    # "latest" (the default), "all", or Polarity versions, comma-separated.
    versions = [v.strip().lower() for v in (os.environ.get("ROOM_VERSIONS", "") or "latest").split(",") if v.strip()]
    return {
        "poll": env_float("POLL_EVERY", 20, 10),
        "refresh": env_float("REFRESH_EVERY", 20, 10),
        "fail_after": env_float("FAIL_AFTER", 120, 0),
        "max_rooms": int(env_float("MAX_ROOMS", 9, 0)),
        "heading": os.environ.get("HEADING", "") or "Polarity public rooms",
        "versions": versions,
        "latest_url": (os.environ.get("LATEST_URL", "") or "https://polaritymod.link/update.json").strip(),
        "latest_every": env_float("LATEST_EVERY", 300, 60),
        "shown": None,
        "latest": None,
        "message_id": os.environ.get("MESSAGE_ID", "").strip() or None,
        "offline_notice": os.environ.get("OFFLINE_NOTICE", "1").strip() not in ("0", "false", "no", ""),
        "notify": os.environ.get("NEW_ROOM_NOTICE", "1").strip() not in ("0", "false", "no", ""),
        "notify_text": os.environ.get("NEW_ROOM_TEXT", "").strip() or "New room opened",
        "notify_delete": env_float("NEW_ROOM_DELETE_AFTER", 5, 1),
        "notify_cooldown": env_float("NEW_ROOM_COOLDOWN", 60, 0),
        "failing_since": 0,
        "trace": os.environ.get("TRACE", "").strip().lower() in ("1", "true", "yes"),
    }


def read_secret():
    path = os.environ.get("DISCORD_SECRET_FILE", "")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            raise SystemExit(f"rooms-bot: cannot read {path} ({e})")
    return os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def main():
    parser = argparse.ArgumentParser(description="Keep a Discord message showing Polarity's public rooms.")
    parser.add_argument("--print", action="store_true", help="render the live list to stdout; no Discord")
    parser.add_argument("--once", action="store_true", help="one update, then exit")
    args = parser.parse_args()

    settings = load_settings()
    api_url = os.environ.get("ROOMS_API_URL", "").strip() or "https://polarity-rooms-api.<account>.workers.dev"
    api = RoomsApi(api_url)

    if args.print:
        sys.stdout.reconfigure(encoding="utf-8")
        rooms = api.get("/v1/rooms")
        summary = api.get("/v1/summary")
        latest = Latest(settings["latest_url"], settings["latest_every"]).get(time.time())
        resolve_versions(settings, latest if "latest" in settings["versions"] else None)
        shown = "stale" if rooms.get("stale") else "live"
        print(json.dumps(render(rooms, summary, shown, settings, time.time()), indent=2, ensure_ascii=False))
        return

    secret = read_secret()
    if not secret:
        raise SystemExit("rooms-bot: no Discord webhook URL (DISCORD_SECRET_FILE)")
    discord = Discord(secret)
    bot = Bot(api, discord, State(os.environ.get("STATE_DIR", "/data")), settings)
    log(f"rooms-bot: {api_url} -> Discord webhook, every {settings['poll']:g} s"
        + (f", message {bot.message_id}" if bot.message_id else ", new message")
        + (", tracing every look" if settings["trace"] else ""))

    if args.once:
        bot.sweep(time.time(), everything=True)
        bot.step(force=True)
        return

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        next_look = 0.0
        while True:
            now = time.time()
            if now >= next_look:
                late = now - next_look if next_look else 0.0
                next_look = now + settings["poll"]
                bot.step(late=late)
            bot.sweep(time.time())
            time.sleep(0.5 if bot.notices else 1.0)
    except Stop:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        bot.stopped()


if __name__ == "__main__":
    main()
