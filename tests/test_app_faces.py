# SPDX-License-Identifier: GPL-2.0-or-later
"""app/ faces for the older players: the face file against the board's key
table, and the window drawn off screen with the mouse driven by posted events."""
import os
import re
import sys
import time

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
pygame = pytest.importorskip("pygame")

EMU = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(EMU, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

import face as FACE  # noqa: E402
import virtual_deck as V  # noqa: E402


def test_every_face_file_loads_with_its_controls_inside_it():
    for name in FACE.available():
        face = FACE.load(name)
        w, h = face.size
        for k in face.keys:
            x0, y0, x1, y1 = (k.circle[0] - k.circle[2], k.circle[1] - k.circle[2],
                              k.circle[0] + k.circle[2], k.circle[1] + k.circle[2]) if k.circle else k.rect
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (name, k.name)


def make_app(model):
    a = V.App(V.parse_args(["--model", model, "--decks", "1", "--panel-port", "1", "--vnc-base", "1", "--hidden"]))
    deck = a.decks[0]
    deck.sent = []
    deck.link.send = lambda tag, dg: deck.sent.append(dg)
    return a


def post(app, kind, **kw):
    kw.setdefault("window", app.window)
    app._event(pygame.event.Event(kind, **kw))


def at(app, x, y):
    s = app.decks[0].scale
    return app._left() + x * s, y * s


def test_every_deck_with_a_face_names_it_in_its_profile():
    from launcher import model
    for mid in FACE.available():
        assert model.load(mid).app_face == mid


def drag_jog(deck, radius_share):
    cx, cy, r = deck.face.jog
    deck.down(((cx + r * radius_share) * deck.scale, cy * deck.scale))
    deck.drag(((cx + r * radius_share * 0.9) * deck.scale, (cy + r * radius_share * 0.43) * deck.scale))
    deck.up((0, 0))


def test_the_jog_and_slider_are_wired_where_they_were_measured():
    measured = {"cdj850"}
    assert {m for m in FACE.available() if FACE.load(m).jog_counter} == measured
    assert {m for m in FACE.available() if FACE.load(m).slider_off is not None} == measured


@pytest.fixture
def cdj850_app():
    a = make_app("cdj850")
    try:
        yield a
    finally:
        a.close()


def test_cdj850_jog_tracks_touch_on_the_top_plate_only(cdj850_app):
    deck = cdj850_app.decks[0]
    drag_jog(deck, 0.75)
    assert "0x0d:32:0:hold" in deck.sent
    assert "0x0d:32:0:rel" in deck.sent
    deck.sent.clear()
    drag_jog(deck, 0.95)
    assert "0x0d:32:0:hold" not in deck.sent


def test_cdj850_vinyl_knob_turns_and_sends_its_analog_level(cdj850_app):
    deck = cdj850_app.decks[0]
    x, y, _ = deck.vinyl_key.circle
    at_knob = (x * deck.scale, y * deck.scale)
    before = pygame.image.tobytes(deck.vinyl_layer[0], "RGBA")
    deck.hover(at_knob)
    assert "brake and release ramps" in cdj850_app.hover_text
    deck.down(at_knob)
    deck.drag((at_knob[0], -100))
    deck.up((at_knob[0], -100))
    assert deck.sent == ["0x18:255:0:lvl"]
    assert pygame.image.tobytes(deck.vinyl_layer[0], "RGBA") != before
    deck.wheel(at_knob, -1)
    assert deck.sent[-1] == "0x18:247:0:lvl"
    deck.rescale(deck.scale, deck.ratio)
    assert deck.vinyl_speed == 247


def test_cdj850_jog_display_uses_panel_state_without_a_lcd_frame(cdj850_app):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    now = time.monotonic()
    deck.link.seen = now
    # MAIN page 1 captures after LOOP IN and after PLAY brakes to a stop.
    deck.link.frame = bytes.fromhex("010f581901001c420080000000000061")
    deck.tick(now)
    active = deck.jog_display
    assert active.position == 24 and active.cue == 14
    assert active.vinyl and active.audio_memory and not active.touch
    before = pygame.image.tobytes(deck.jog_display_layer[0], "RGBA")

    deck.link.frame = bytes.fromhex("010f784001001a2a008000000000008e")
    deck.tick(now)
    assert deck.jog_display.position == 63 and deck.jog_display.touch
    assert pygame.image.tobytes(deck.jog_display_layer[0], "RGBA") != before
    layer = deck.jog_display_layer
    deck.tick(now + 1)
    assert deck.jog_display_layer is layer

    # Firmware turns the memory ring off briefly while buffering the track.
    deck.link.frame = bytes.fromhex("0100180101001f0000800000000000ba")
    deck.tick(now + 1)
    assert deck.jog_display.position == 0 and not deck.jog_display.audio_memory
    assert deck.jog_display.vinyl
    deck.link.frame = bytes.fromhex("01004027010015250080000000000024")
    deck.tick(now + 1)
    assert deck.jog_display.audio_memory and not deck.jog_display.vinyl
    deck.tick(now + 4)
    assert deck.jog_display_layer is None


def test_cdj850_mode_lamps_and_rev_button_follow_firmware(cdj850_app):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    rev = deck.face.key_named("DIRECTION REV")
    x0, y0, x1, y1 = rev.rect
    pos = ((x0 + x1) * deck.scale / 2, (y0 + y1) * deck.scale / 2)
    rev_pixel = (round((x0 + 2) * deck.px), round((y0 + y1) * deck.px / 2))
    modes = {rev.name, "VINYL", "MASTER TEMPO"}

    def show(frame_hex):
        now = time.monotonic()
        deck.link.page2 = bytes.fromhex(frame_hex), now
        deck.tick(now)
        deck.compose()
        return deck.lit & modes

    assert show("0200581101f2204f00040010d02f4a2d") == {"VINYL"}
    dark_red = deck.surf.get_at(rev_pixel).r
    assert show("0200582702f2304f00040090d02f4ad4") == {"VINYL", "MASTER TEMPO"}
    assert show("0200404f03f2104f00020090d02f4ac3") == {"MASTER TEMPO"}

    # Each click sends a fresh press and release. The lamp follows the report.
    deck.down(pos)
    deck.up(pos)
    assert rev.name not in deck.key_layers
    assert show("0200401006f0184f00060090d02f4a91") == {rev.name, "MASTER TEMPO"}
    assert deck.surf.get_at(rev_pixel).r > dark_red
    assert show("0200584003f0384f00040090d02f4af4") == modes
    assert show("0200581900f2284f00040010d02f4a3c") == {rev.name, "VINYL"}
    deck.down(pos)
    deck.up(pos)
    assert rev.name in deck.key_layers
    assert show("0200580101f2204f00040010d02f4a1d") == {"VINYL"}
    assert deck.surf.get_at(rev_pixel).r == dark_red
    assert deck.sent == ["0x12:32:0:hold", "0x12:32:0:rel"] * 2


def test_cdj850_transport_lamps_follow_captured_page_two(cdj850_app, monkeypatch):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    beat_lamps = {f"AUTO BEAT LOOP {beats}" for beats in (1, 2, 4, 8)}
    transport = beat_lamps | {"PLAY/PAUSE", "CUE", "LOOP IN", "LOOP OUT", "RELOOP/EXIT"}

    def show(frame_hex):
        now = time.monotonic()
        deck.link.page2 = bytes.fromhex(frame_hex), now
        deck.tick(now)
        return deck.lit & transport

    draw = deck.art._k_big
    painted = []

    def record(p, key, lit, down):
        painted.append((key.name, lit, down))
        draw(p, key, lit, down)

    monkeypatch.setattr(deck.art, "_k_big", record)
    assert show("0200583602f2204f00040010d02f4a53") == beat_lamps | {
        "PLAY/PAUSE", "LOOP IN", "LOOP OUT"}
    key = deck.face.key_named("PLAY/PAUSE")
    pos = tuple(x * deck.scale for x in key.circle[:2])
    deck.down(pos)
    # PLAY/PAUSE turns off on the next pause phase, even with the button held.
    assert show("0200783107e8204f00060010d02f4a6b") == beat_lamps | {
        "CUE", "LOOP IN", "LOOP OUT"}
    assert ("PLAY/PAUSE", False, True) in painted
    deck.up(pos)
    assert "PLAY/PAUSE" not in deck.key_layers
    assert "PLAY/PAUSE" in show("021f583107f8204f00020810d82f4a86")
    # Fine-adjusting the cue point has CUE on and off phases of its own.
    assert "CUE" in show("021f788705ea204f00020810d82f4aec")
    assert "CUE" not in show("021f788705e0204f00020810d82f4ae2")

    assert show("023058050bb8214f00060a10d82f4a36") == beat_lamps | {
        "PLAY/PAUSE", "CUE", "LOOP IN", "RELOOP/EXIT"}
    assert show("023058590998214700060a10d82f4a60") == (beat_lamps - {"AUTO BEAT LOOP 8"}) | {
        "PLAY/PAUSE", "CUE", "RELOOP/EXIT"}
    assert show("0230580d0cd8214f00020a10d82f4a5b") == beat_lamps | {
        "PLAY/PAUSE", "CUE", "LOOP OUT", "RELOOP/EXIT"}
    assert show("020c58361398214b00061310d82f4a30") == (beat_lamps - {"AUTO BEAT LOOP 4"}) | {
        "PLAY/PAUSE", "CUE", "RELOOP/EXIT"}


def test_cdj850_source_and_usb_activity_lamps_follow_captured_page_two(cdj850_app):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    activity = deck.face.key_named("USB ACTIVITY")
    x0, y0, x1, y1 = activity.rect
    pixel = tuple(round(v * deck.px / 2) for v in (x0 + x1, y0 + y1))
    sources = {"DISC", "USB", "PC", activity.name}

    def show(frame_hex):
        now = time.monotonic()
        deck.link.page2 = bytes.fromhex(frame_hex), now
        deck.tick(now)
        deck.compose()
        return deck.lit & sources

    # The stick can be accessed even while DISC is selected.
    assert show("020058000062221f00000004502f4acb") == {"DISC", activity.name}
    lit_red = deck.surf.get_at(pixel).r
    assert show("020058000060221f00000004502f4ac9") == {"DISC"}
    assert deck.surf.get_at(pixel).r < lit_red
    assert show("0200580000e2201f00000004502f4a4a") == {"USB", activity.name}
    # After USB STOP, USB remains selected while the activity light goes out.
    assert show("0200580000e0204000000000d02f4ae5") == {"USB"}
    # PC flashes while the firmware asks for confirmation.
    assert show("0200584208f4204f00060010d02f4a69") == {"USB", "PC"}
    assert show("0200584c09f0204f00060010d02f4a70") == {"USB"}
    assert show("020058880064200000000000502f4a31") == {"PC"}
    pos = ((x0 + x1) * deck.scale / 2, (y0 + y1) * deck.scale / 2)
    deck.down(pos)
    deck.up(pos)
    assert deck.sent == []


def test_cdj850_navigation_buttons_light_from_page_two(cdj850_app):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    navigation = {"BROWSE", "TAG LIST", "INFO", "MENU"}
    x0, y0, x1, y1 = deck.face.key_named("BROWSE").rect
    pixel = (round((x0 + x1) * deck.px / 2), round((y0 + 0.35) * deck.px))

    def show(frame_hex, expected):
        now = time.monotonic()
        deck.link.page2 = bytes.fromhex(frame_hex), now
        deck.tick(now)
        deck.compose()
        assert deck.lit & navigation == expected

    show("0200581101f2204f00040010d02f4a2d", {"INFO"})
    dark = deck.surf.get_at(pixel).g
    pos = ((x0 + x1) * deck.scale / 2, (y0 + y1) * deck.scale / 2)
    deck.down(pos)
    deck.compose()
    held = deck.surf.get_at(pixel).g
    deck.up(pos)

    show("0200582202f2201f00040014502f4a92", {"BROWSE"})
    assert deck.surf.get_at(pixel).g > max(dark, held)
    show("0200585303f2202f00020018502f4ad6", {"TAG LIST"})
    assert deck.surf.get_at(pixel).g == dark
    # LIST MENU keeps TAG LIST lit. UTILITY lights MENU on its own.
    show("0200584708f020af00060018502f4a52", {"TAG LIST", "MENU"})
    show("0200583a0ef0208f00020018502f4a27", {"MENU"})
    show("020058880064200000000000502f4a31", set())


def test_cdj850_eject_and_standby_lamps_follow_firmware(cdj850_app):
    deck = cdj850_app.decks[0]
    deck.screen.take_frame = lambda: None
    eject = deck.face.key_named("DISC EJECT")
    standby = deck.face.key_named("STANDBY")
    eject_pos = tuple(v * deck.scale for v in eject.circle[:2])
    standby_pos = tuple(v * deck.scale for v in standby.circle[:2])
    standby_pixel = tuple(round(v * deck.px) for v in standby.circle[:2])
    lamps = {eject.name, standby.name}

    def show(frame_hex, expected):
        now = time.monotonic()
        deck.link.page2 = bytes.fromhex(frame_hex), now
        deck.tick(now)
        deck.compose()
        assert deck.lit & lamps == expected

    # The homing sequence flashes the eject symbol and then leaves it dark.
    show("0200588a0060621f00000004502f4a94", {eject.name})
    show("0200588a0060221f00000004502f4a54", set())
    idle_red = deck.surf.get_at(standby_pixel).r

    deck.down(eject_pos)
    assert eject.name not in deck.lit
    show("020058000060621f00000004502f4a0a", {eject.name})
    show("0200588a0060221f00000004502f4a54", set())
    deck.up(eject_pos)

    # MAIN requests standby only after the idle timer expires.
    show("020058000060221f01000004502f4aca", {standby.name})
    assert deck.surf.get_at(standby_pixel).r > idle_red
    deck.down(standby_pos)
    deck.up(standby_pos)
    assert deck.sent == ["0x13:1:0:hold", "0x13:1:0:rel"]
    show("020058000060221f00000004402f4ab9", set())
    assert deck.surf.get_at(standby_pixel).r == idle_red


@pytest.mark.parametrize("model", FACE.available())
def test_no_label_or_key_runs_into_the_jog_surround(model):
    import math
    import art
    face = FACE.load(model)
    cx, cy, r = face.jog
    labels = [(x, y, text, size) for x, y, text, size in face.printed]
    labels += [(x, y, text, 7.5) for x, y, text in face.pills + face.frames]
    for x, y, text, size in labels:
        w = max(art.text_width(line, size) for line in text.split("\n"))
        h = size * 1.12 * (text.count("\n") + 1)
        nearest = math.hypot(min(max(cx, x - w / 2), x + w / 2) - cx, min(max(cy, y - h / 2), y + h / 2) - cy)
        assert nearest >= r, (model, text)
    for k in face.keys:
        if k.circle:
            assert math.hypot(k.circle[0] - cx, k.circle[1] - cy) - k.circle[2] >= r, (model, k.name)


@pytest.mark.parametrize("model", sorted(FACE.available()))
def test_no_control_straddles_a_seam_between_the_panel_plates(model):
    face = FACE.load(model)
    x0, y0, x1, y1 = face.panel
    h = face.size[1]
    boxes = {k.name: k.rect or (k.circle[0] - k.circle[2], k.circle[1] - k.circle[2],
                                k.circle[0] + k.circle[2], k.circle[1] + k.circle[2]) for k in face.keys}
    if face.has_key("JOG MODE"):
        boxes["JOG MODE box"] = face.jog_mode_box
    for name, (a, b, c, d) in boxes.items():
        for seam in (x0, x1):
            assert not (a < seam < c and d > y0 and b < h), (model, name, "crosses the side plate edge")
        assert not (a < x1 and c > x0 and b < y1 < d), (model, name, "crosses the panel's lower edge")


def test_the_panel_link_reads_the_frame_main_publishes():
    import socket
    import panel_link
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    link = panel_link.PanelLink(1, port)
    link.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as main:
            deadline = time.monotonic() + 5
            while not link.lit(0x05, 0x10, 2) and time.monotonic() < deadline:
                main.sendto(b"pnl 0201" + b"00" * 30, ("127.0.0.1", port))
                main.sendto(b"pnl2 0200583602f2204f00040010d02f4a53", ("127.0.0.1", port))
                time.sleep(0.05)
        assert link.lit(0x00, 0x02) and link.lit(0x01, 0x01) and not link.lit(0x00, 0x01)
        assert link.lit(0x05, 0x10, 2) and not link.lit(0x05, 0x10)
        frame, seen = link.page2
        link.page2 = frame, seen - 4
        assert link.lit(0x00, 0x02) and not link.lit(0x05, 0x10, 2)
    finally:
        link.close()
