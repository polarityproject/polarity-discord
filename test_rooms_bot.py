#!/usr/bin/env python3
# Tests for rooms_bot.py against a stand-in rooms API and a stand-in Discord
# webhook on 127.0.0.1 (no network):  python test_rooms_bot.py
import contextlib
import http.server
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest

import rooms_bot as bot


def room(code="4SLJEV", **kw):
    doc = {
        "code": code, "title": "Karin user looking for M. Bison!", "title_id": 3,
        "title_characters": ["KRN", "VEG"], "owner": "POLARITYDEV", "client_version": "0.3.0",
        "members": 3, "capacity": 16, "tables": 2, "open_seats": 2,
        "rules": "r1 ft2 bo3 t99 ws3", "faces": ["RYU", "KEN"],
        "players": [
            {"name": "POLARITYDEV", "country": "DE", "face": "RYU"},
            {"name": "Player 4821", "country": "FR", "face": "KEN"},
            {"name": "DECK", "country": None, "face": None},
        ],
    }
    doc.update(kw)
    return doc


def rooms_doc(rooms, stale=False, updated=None):
    return {"version": 1, "updated": updated or int(time.time() * 1000), "age": 4, "stale": stale,
            "attribution": "IP Geolocation by DB-IP (https://db-ip.com), CC BY 4.0", "rooms": rooms}


def summary_doc(online=12, rooms=()):
    return {"version": 1, "updated": int(time.time() * 1000), "age": 4, "stale": False,
            "totals": {"online": online, "in_public_rooms": sum(r["members"] for r in rooms),
                       "public_rooms": len(rooms)}}


SETTINGS = dict(bot.load_settings(), poll=20.0, refresh=300.0, fail_after=0.0, versions=["all"], notify=False,
                latest_url="")
NOTIFY = dict(notify=True, notify_text="New room opened", notify_delete=5.0, notify_cooldown=60.0)


def total_chars(payload):
    return sum(bot.embed_size(e) for e in payload["embeds"])


class Render(unittest.TestCase):
    def test_spec_example(self):
        r = room()
        p = bot.render(rooms_doc([r]), summary_doc(12, [r]), "live", SETTINGS, 1000)
        head, card = p["embeds"]
        self.assertIn("**12** online", head["description"])
        self.assertIn("**1** public room ·", head["description"])
        self.assertIn("<t:1000:R>", head["description"])
        self.assertNotIn("footer", head)
        self.assertEqual(card["title"], "Karin user looking for M. Bison!")
        self.assertIn("**Code `4SLJEV`**", card["description"])
        self.assertIn("**Code `4SLJEV`** · Polarity 0.3.0\n3/16 players · 2 tables", card["description"])
        self.assertIn("Table 1 rules: First to 2 · winner stays, up to 3 · 99 seconds *(other tables may differ)*",
                      card["description"])
        one = bot.room_embed(room(tables=1), 16)["description"]
        self.assertIn("\nRules: First to 2 · winner stays", one)
        self.assertIn("🇩🇪 **POLARITYDEV** 👑 — Ryu", card["description"])
        self.assertIn("🇫🇷 Player 4821 — Ken", card["description"])
        self.assertIn("\nDECK", card["description"])  # no flag, no character
        self.assertEqual(card["color"], bot.COLOR_LIVE)
        self.assertEqual(p["allowed_mentions"], {"parse": []})

    def test_null_title_and_no_countries(self):
        r = room(title=None, title_id=0, title_characters=[], owner="Bob_*",
                 players=[{"name": "Bob_*", "country": None, "face": None}])
        p = bot.render(rooms_doc([r]), summary_doc(1, [r]), "live", SETTINGS, 1)
        self.assertEqual(p["embeds"][1]["title"], "Bob\\_\\*'s room")

    def test_empty(self):
        p = bot.render(rooms_doc([]), summary_doc(4), "live", SETTINGS, 1)
        self.assertEqual(len(p["embeds"]), 1)
        self.assertIn("No public rooms open", p["embeds"][0]["description"])

    def test_stale_hides_rooms(self):
        doc = rooms_doc([room()], stale=True, updated=1794106181484)
        p = bot.render(doc, summary_doc(), "stale", SETTINGS, 1)
        self.assertEqual(len(p["embeds"]), 1)
        self.assertIn("not reported since <t:1794106181:R>", p["embeds"][0]["description"])
        self.assertEqual(p["embeds"][0]["color"], bot.COLOR_DOWN)

    def test_names_are_text(self):
        r = room(owner="x", players=[{"name": "<@123> @everyone **b** `c` > q\nnew", "country": "JP", "face": "ZZZ"}])
        desc = bot.render(rooms_doc([r]), None, "live", SETTINGS, 1)["embeds"][1]["description"]
        self.assertIn("\\<\\@123\\> \\@everyone \\*\\*b\\*\\* \\`c\\` \\> q new — ZZZ", desc)
        self.assertNotIn("<@", desc)

    def test_rules(self):
        self.assertEqual(bot.describe_rules("r1 ft1 bo1 t30 rr"), "First to 1 · round robin · 30 seconds · one round")
        self.assertEqual(bot.describe_rules("r1 ft3 bo5 t60 ls0"), "First to 3 · loser stays · 60 seconds · best of 5")
        self.assertEqual(bot.describe_rules("r2 whatever"), "r2 whatever")
        self.assertEqual(bot.describe_rules("r1 ft2 zz9"), "First to 2 · zz9")

    def test_full_rooms_fit(self):
        players = [{"name": "N" * 32, "country": "US", "face": "Z42"} for _ in range(16)]
        rooms = [room(code=f"ABCDE{c}", title="T" * 40, members=16, open_seats=0, players=players)
                 for c in "ABCDEFGHJKLM"]
        p = bot.render(rooms_doc(rooms), summary_doc(200, rooms), "live", SETTINGS, 1)
        self.assertLessEqual(len(p["embeds"]), 10)
        self.assertLessEqual(total_chars(p), 6000)
        self.assertIn("more room", p["embeds"][0]["description"])
        self.assertEqual(p["embeds"][1]["color"], bot.COLOR_FULL)

    def test_table_list(self):
        # Every table with its rules and seats (keepers from 0.4.0); players
        # not seated listed after them.
        tables = [
            {"table": 1, "rules": "r1 ft2 bo3 t99 ws3", "state": "playing",
             "seats": [{"name": "POLARITYDEV", "country": "DE", "face": "RYU"},
                       {"name": "Player 4821", "country": "FR", "face": "KEN"}], "score": [1, 0]},
            {"table": 2, "rules": "r1 ft3 bo3 t60 rr", "state": "open", "seats": [None, None], "score": [0, 0]},
            {"table": 3, "rules": "r1 ft2 bo3 t99 ws3", "state": "open",
             "seats": [{"name": "DECK", "country": None, "face": None}, None], "score": [0, 0]},
        ]
        desc = bot.room_embed(room(tables=3, table_list=tables), 16)["description"]
        self.assertIn("**Table 1** · First to 2 · winner stays, up to 3 · 99 seconds\n"
                      "🇩🇪 POLARITYDEV (Ryu) vs 🇫🇷 Player 4821 (Ken) · playing, 1–0", desc)
        # Table 2 (empty) is counted after table 3 (one seat taken).
        self.assertNotIn("**Table 2**", desc)
        self.assertIn("**Table 3** · First to 2 · winner stays, up to 3 · 99 seconds\nDECK vs *open seat*\n"
                      "**1 Open Table**", desc)
        self.assertNotIn("other tables may differ", desc)
        # Everyone is seated: no players list after the tables.
        self.assertNotIn("Also in the room", desc)
        # Someone not seated is listed after them.
        extra = room(tables=3, table_list=tables,
                     players=room()["players"] + [{"name": "Lurker", "country": "JP", "face": "Z21"}])
        desc = bot.room_embed(extra, 16)["description"]
        self.assertIn("**Also in the room**\n🇯🇵 Lurker — Akuma", desc)
        # Squeezed (no players shown): one line a table.
        tight = bot.room_embed(room(tables=3, table_list=tables), 0)["description"]
        self.assertIn("**Table 1:** 🇩🇪 POLARITYDEV (Ryu) vs 🇫🇷 Player 4821 (Ken) · playing, 1–0", tight)
        self.assertIn("**Table 3:** DECK vs *open seat*\n**1 Open Table**", tight)
        # Several empty tables, one in a gap: one count; all empty: the count alone.
        gaps = [dict(tables[0]), dict(tables[1]), dict(tables[0], table=3), dict(tables[1], table=4)]
        desc = bot.room_embed(room(tables=4, table_list=gaps), 16)["description"]
        self.assertIn("**Table 3**", desc)
        self.assertIn("**2 Open Tables**", desc)
        self.assertLess(desc.index("**Table 3**"), desc.index("**2 Open Tables**"))
        idle = [dict(tables[1], table=n) for n in (1, 2, 3)]
        desc = bot.room_embed(room(tables=3, table_list=idle, players=[]), 16)["description"]
        self.assertTrue(desc.endswith("\n\n**3 Open Tables**"), desc)

    def test_old_version_field(self):
        # A keeper before 0.4.0 sends sfvr_version only.
        old = room(code="AAAAAA", sfvr_version="0.3.1")
        del old["client_version"]
        self.assertEqual(bot.room_version(old), "0.3.1")
        s = dict(SETTINGS, versions=["0.3.1"])
        bot.resolve_versions(s, None)
        p = bot.render(rooms_doc([old]), None, "live", s, 1)
        self.assertIn("AAAAAA", p["embeds"][1]["description"])

    def test_version_filter(self):
        rooms = [room(code="AAAAAA", client_version="0.3.0"), room(code="BBBBBB", client_version="0.3.1")]
        s = dict(SETTINGS, versions=["0.3.1"])
        bot.resolve_versions(s, None)
        p = bot.render(rooms_doc(rooms), None, "live", s, 1)
        self.assertEqual(len(p["embeds"]), 2)
        self.assertIn("BBBBBB", p["embeds"][1]["description"])
        self.assertNotIn("Polarity 0.3.1", p["embeds"][1]["description"])  # one version: not on each card
        self.assertIn("Rooms on Polarity **0.3.1** (1 room on other versions not listed).", p["embeds"][0]["description"])

    def test_latest_version(self):
        rooms = [room(code="AAAAAA", client_version="0.3.0"), room(code="BBBBBB", client_version="0.3.0.1")]
        s = dict(SETTINGS, versions=["latest"])
        bot.resolve_versions(s, "0.3.0.1")
        p = bot.render(rooms_doc(rooms), summary_doc(9, rooms), "live", s, 1)
        head = p["embeds"][0]["description"]
        self.assertEqual(len(p["embeds"]), 2)
        self.assertIn("**1** public room · **3** in them", head)
        self.assertIn("Rooms on Polarity **0.3.0.1**, the latest version (1 room on other versions not listed).", head)
        # Not known yet: every version, each card naming its own.
        bot.resolve_versions(s, None)
        p = bot.render(rooms_doc(rooms), None, "live", s, 1)
        self.assertEqual(len(p["embeds"]), 3)
        self.assertIn("**Code `AAAAAA`** · Polarity 0.3.0\n", p["embeds"][1]["description"])
        self.assertNotIn("Rooms on Polarity", p["embeds"][0]["description"])
        # Latest plus an older one.
        s = dict(SETTINGS, versions=["latest", "0.3.0"])
        bot.resolve_versions(s, "0.3.0.1")
        p = bot.render(rooms_doc(rooms), None, "live", s, 1)
        self.assertEqual(len(p["embeds"]), 3)
        self.assertIn("Rooms on Polarity **0.3.0**, **0.3.0.1**.", p["embeds"][0]["description"])

    def test_default_setting_is_latest(self):
        old = os.environ.pop("ROOM_VERSIONS", None)
        try:
            self.assertEqual(bot.load_settings()["versions"], ["latest"])
            os.environ["ROOM_VERSIONS"] = "All"
            self.assertEqual(bot.load_settings()["versions"], ["all"])
        finally:
            os.environ.pop("ROOM_VERSIONS", None)
            if old is not None:
                os.environ["ROOM_VERSIONS"] = old

    def test_checked_time_alone_is_no_change(self):
        a = bot.render(rooms_doc([room()]), None, "live", SETTINGS, 1000)
        b = bot.render(rooms_doc([room()]), None, "live", SETTINGS, 2000)
        self.assertEqual(bot.strip_checked(a), bot.strip_checked(b))


class Fake(http.server.BaseHTTPRequestHandler):
    """The rooms API on /v1/..., a webhook on /api/webhooks/1/tok..."""
    world = None

    def _send(self, code, doc=None):
        body = json.dumps(doc).encode() if doc is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")

    def do_GET(self):
        w = self.world
        if w["api_down"]:
            return self._send(503, {"error": "no room list yet"})
        if self.path == "/v1/rooms":
            return self._send(200, w["rooms"])
        if self.path == "/v1/summary":
            return self._send(200, w["summary"])
        if self.path == "/update.json":
            w["update_reads"] += 1
            if w["update"] is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, w["update"])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        w = self.world
        body = self._body()
        if not self.path.startswith("/api/webhooks/1/tok"):
            return self._send(401, {"code": 50027, "message": "Invalid Webhook Token"})
        if "wait=true" not in self.path:
            return self._send(204)
        w["next_id"] += 1
        mid = str(w["next_id"])
        w["messages"][mid] = body
        w["log"].append(("POST", mid))
        self._send(200, {"id": mid})

    def do_PATCH(self):
        w = self.world
        m = re.match(r"/api/webhooks/1/tok/messages/(\d+)", self.path)
        body = self._body()  # read before any answer, or the client can see a reset
        if w["limit_once"]:
            w["limit_once"] = False
            return self._send(429, {"message": "You are being rate limited.", "retry_after": 0.2, "global": False})
        if not m or m.group(1) not in w["messages"]:
            return self._send(404, {"code": 10008, "message": "Unknown Message"})
        w["messages"][m.group(1)] = body
        w["log"].append(("PATCH", m.group(1)))
        self._send(200, {"id": m.group(1)})

    def do_DELETE(self):
        w = self.world
        m = re.match(r"/api/webhooks/1/tok/messages/(\d+)", self.path)
        if w["delete_fails"]:
            return self._send(500, {"message": "oops"})
        if not m or m.group(1) not in w["messages"]:
            return self._send(404, {"code": 10008, "message": "Unknown Message"})
        del w["messages"][m.group(1)]
        w["log"].append(("DELETE", m.group(1)))
        self._send(204)

    def log_message(self, *args):
        pass


class Loop(unittest.TestCase):
    def setUp(self):
        self.world = {"rooms": rooms_doc([room()]), "summary": summary_doc(5, [room()]), "api_down": False,
                      "messages": {}, "next_id": 100, "log": [], "limit_once": False, "delete_fails": False,
                      "update": {"product": "Polarity", "version": "0.3.0.1"}, "update_reads": 0}
        Fake.world = self.world
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{port}"
        self.dir = tempfile.mkdtemp()
        self.make()

    def make(self, **settings):
        self.bot = bot.Bot(bot.RoomsApi(self.base), bot.Discord(self.base + "/api/webhooks/1/tok"),
                           bot.State(self.dir), dict(SETTINGS, **settings))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def last(self):
        return self.world["messages"][self.bot.message_id]

    def test_post_then_edit_only_on_change(self):
        self.bot.step()
        self.assertEqual(self.world["log"], [("POST", "101")])
        self.bot.step()
        self.bot.step()
        self.assertEqual(len(self.world["log"]), 1)  # nothing changed: no edit
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()
        self.assertEqual(self.world["log"][-1], ("PATCH", "101"))
        self.assertEqual(len(self.last()["embeds"]), 3)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "healthy")))

    def test_refresh_edits_when_due(self):
        self.make(refresh=0.0)
        self.bot.step()
        self.bot.step()
        self.assertEqual(self.world["log"], [("POST", "101"), ("PATCH", "101")])

    def test_default_refresh_edits_every_look(self):
        self.assertEqual(bot.load_settings()["refresh"], 20.0)
        self.make(refresh=20.0)
        self.bot.step()
        self.bot.last_edit -= 19.0  # a look later, the calls a little faster this time
        self.bot.step()
        self.assertEqual(self.world["log"], [("POST", "101"), ("PATCH", "101")])
        self.bot.last_edit -= 5.0  # a quick extra look (force is the only other edit)
        self.bot.step()
        self.assertEqual(len(self.world["log"]), 2)

    def test_restart_keeps_message_and_deleted_is_reposted(self):
        self.bot.step()
        self.make()  # a restart: reads state.json
        self.assertEqual(self.bot.message_id, "101")
        self.bot.step(force=True)
        self.assertEqual(self.world["log"][-1], ("PATCH", "101"))
        del self.world["messages"]["101"]  # someone deleted it
        self.bot.step(force=True)
        self.assertEqual(self.world["log"][-1], ("POST", "102"))
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["message_id"], "102")

    def test_rate_limit_is_waited_out(self):
        self.bot.step()
        self.world["limit_once"] = True
        self.bot.step(force=True)
        self.assertEqual(self.world["log"][-1], ("PATCH", "101"))

    def test_api_down_and_stale(self):
        self.make(fail_after=60.0)
        self.bot.step()
        self.world["api_down"] = True
        self.bot.step()  # a short outage keeps the list
        self.assertEqual(len(self.world["log"]), 1)
        self.bot.failing_since -= 61
        self.bot.step()
        self.assertIn("cannot be reached", self.last()["embeds"][0]["description"])
        self.assertEqual(len(self.last()["embeds"]), 1)
        self.world["api_down"] = False
        self.world["rooms"] = rooms_doc([room()], stale=True, updated=int((time.time() - 300) * 1000))
        self.bot.step()
        self.assertIn("has not reported since", self.last()["embeds"][0]["description"])
        self.world["rooms"] = rooms_doc([room()])
        self.bot.step()
        self.assertEqual(len(self.last()["embeds"]), 2)

    def test_old_updated_counts_as_stale(self):
        self.world["rooms"] = rooms_doc([room()], updated=int((time.time() - 600) * 1000))
        self.bot.step()
        self.assertIn("has not reported", self.last()["embeds"][0]["description"])

    def logged(self, fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn()
        return out.getvalue()

    def test_trace_names_each_call_without_the_token(self):
        self.make(trace=True)
        text = self.logged(self.bot.step)
        self.assertRegex(text, r"look: live, 1 room, posted in [\d.]+ s; list \d+ s old \(age 4\); "
                               r"GET rooms [\d.]+ s, GET summary [\d.]+ s, POST webhook [\d.]+ s")
        text = self.logged(lambda: self.bot.step(late=7.4))
        self.assertIn("look (7 s late): live, 1 room, no change in", text)
        self.assertNotIn("PATCH", text)
        self.world["api_down"] = True
        self.assertIn("GET rooms", self.logged(self.bot.step))
        self.assertIn("(503)", self.logged(self.bot.step))
        self.assertNotIn("tok", self.logged(lambda: self.bot.step(force=True)))

    def test_untraced_logs_only_slow_calls_and_looks(self):
        self.assertNotIn("look:", self.logged(self.bot.step))
        old = bot.SLOW_CALL, bot.SLOW_LOOK
        bot.SLOW_CALL = bot.SLOW_LOOK = 0.0
        try:
            text = self.logged(lambda: self.bot.step(force=True))
        finally:
            bot.SLOW_CALL, bot.SLOW_LOOK = old
        self.assertIn("slow call: PATCH message took", text)
        self.assertIn("look: live, 1 room, edited (refresh) in", text)
        self.assertNotIn("tok", text)

    def test_list_falling_behind_is_logged(self):
        self.world["rooms"] = rooms_doc([room()], updated=int((time.time() - 70) * 1000))
        self.assertIn("the rooms list is 70 s old", self.logged(self.bot.step))
        self.assertNotIn("rooms list", self.logged(self.bot.step))  # once
        self.world["rooms"] = rooms_doc([room()])
        self.assertRegex(self.logged(self.bot.step), r"fresh again \(0 s old; it had not moved for 7\d s\)")

    def notices(self):
        return [mid for mid, m in self.world["messages"].items() if m.get("content") == "New room opened"]

    def test_new_room_notice_posted_then_deleted(self):
        self.make(**NOTIFY)
        self.bot.step()  # the rooms open at the start: no notice
        self.assertEqual(self.notices(), [])
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()
        self.assertEqual(self.world["log"][-2:], [("PATCH", "101"), ("POST", "102")])  # list first
        self.assertEqual(self.notices(), ["102"])
        self.assertEqual(self.world["messages"]["102"]["allowed_mentions"], {"parse": []})
        self.bot.sweep(time.time())  # not yet
        self.assertEqual(self.notices(), ["102"])
        self.bot.sweep(time.time() + 6)
        self.assertEqual(self.notices(), [])
        self.assertEqual(self.world["log"][-1], ("DELETE", "102"))
        self.assertEqual(self.bot.message_id, "101")  # the list itself stays
        with open(os.path.join(self.dir, "state.json")) as f:
            self.assertEqual(json.load(f)["notices"], [])

    def test_closing_room_and_cooldown(self):
        self.make(**NOTIFY)
        self.bot.step()
        self.world["rooms"] = rooms_doc([])
        self.bot.step()  # a room closing: no notice
        self.assertEqual(self.notices(), [])
        self.world["rooms"] = rooms_doc([room(code="AAAAAA")])
        self.bot.step()
        self.world["rooms"] = rooms_doc([room(code="AAAAAA"), room(code="BBBBBB")])
        self.bot.step()  # within the cooldown: listed, no second notice
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(len(self.last()["embeds"]), 3)

    def test_no_notice_after_outage_or_with_version_filter(self):
        self.make(**NOTIFY, versions=["0.3.0"])
        self.bot.step()
        self.world["rooms"] = rooms_doc([room()], stale=True, updated=int((time.time() - 300) * 1000))
        self.bot.step()
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()  # back from stale: what is there is not "new"
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ"), room(code="OLDOLD", client_version="0.2.6")])
        self.bot.step()  # not a listed version
        self.assertEqual(self.notices(), [])

    def test_notice_left_by_a_restart_is_deleted(self):
        self.make(**NOTIFY)
        self.bot.step()
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()
        self.assertEqual(self.notices(), ["102"])
        self.make(**NOTIFY)  # a restart before its deletion
        self.bot.sweep(time.time())
        self.assertEqual(self.notices(), [])

    def test_delete_retries_then_gives_up(self):
        self.make(**NOTIFY)
        self.bot.step()
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()
        self.world["delete_fails"] = True
        t = time.time() + 6
        for i in range(5):
            self.bot.sweep(t)
            t += 30
        self.assertEqual(self.bot.notices, [])
        self.world["delete_fails"] = False

    def test_stop_deletes_pending_notices(self):
        self.make(**NOTIFY)
        self.bot.step()
        self.world["rooms"] = rooms_doc([room(), room(code="ZZZZZZ")])
        self.bot.step()
        self.bot.stopped()
        self.assertEqual(self.notices(), [])
        self.assertIn("not updating", self.last()["embeds"][0]["description"])

    def test_latest_from_update_info(self):
        self.world["rooms"] = rooms_doc([room(code="AAAAAA", client_version="0.3.0"),
                                         room(code="BBBBBB", client_version="0.3.0.1")])
        self.make(versions=["latest"], latest_url=self.base + "/update.json", latest_every=300.0, **NOTIFY)
        self.bot.step()
        self.assertEqual(len(self.last()["embeds"]), 2)
        self.assertIn("BBBBBB", self.last()["embeds"][1]["description"])
        self.bot.step()
        self.assertEqual(self.world["update_reads"], 1)  # read every 300 s, not every look
        # A release: 0.3.1 is the latest; its rooms are listed (and announced as new).
        # The old site's update info (product SFVR) is read too.
        self.world["update"] = {"product": "SFVR", "version": "0.3.1"}
        self.world["rooms"] = rooms_doc([room(code="BBBBBB", client_version="0.3.0.1"),
                                         room(code="CCCCCC", client_version="0.3.1")])
        self.bot.latest.next = 0
        self.bot.step()
        self.assertEqual(len(self.last()["embeds"]), 2)
        self.assertIn("CCCCCC", self.last()["embeds"][1]["description"])
        self.assertEqual(len(self.notices()), 1)
        # The site down: the last version read is kept.
        self.world["update"] = None
        self.bot.latest.next = 0
        self.bot.step(force=True)
        self.assertIn("**0.3.1**, the latest", self.last()["embeds"][0]["description"])

    def test_latest_unknown_lists_everything(self):
        self.world["update"] = {"product": "other", "version": "9.9"}
        self.world["rooms"] = rooms_doc([room(code="AAAAAA", client_version="0.3.0"),
                                         room(code="BBBBBB", client_version="0.3.0.1")])
        self.make(versions=["latest"], latest_url=self.base + "/update.json", latest_every=300.0)
        self.bot.step()
        self.assertEqual(len(self.last()["embeds"]), 3)
        self.assertLessEqual(self.bot.latest.next - time.time(), 61)  # asks again sooner

    def test_stopped_notice(self):
        self.bot.step()
        self.bot.stopped()
        self.assertIn("not updating", self.last()["embeds"][0]["description"])

    def test_bad_webhook_is_logged_not_fatal(self):
        self.bot = bot.Bot(bot.RoomsApi(self.base), bot.Discord(self.base + "/api/webhooks/1/wrong"),
                           bot.State(self.dir), dict(SETTINGS))
        self.bot.step()
        self.assertTrue(self.bot.discord_failing)
        self.assertIsNone(self.bot.message_id)

    def test_secret_must_be_a_webhook(self):
        with self.assertRaises(SystemExit):
            bot.Discord("MTIz.abc.def")
        d = bot.Discord("https://discord.com/api/webhooks/42/tok?thread_id=7")
        self.assertEqual(d.target, "webhook:42:7")
        self.assertEqual(d._url("/messages/1"), "https://discord.com/api/webhooks/42/tok/messages/1?thread_id=7")


if __name__ == "__main__":
    unittest.main(verbosity=1)
