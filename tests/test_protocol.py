"""Frame layouts checked byte-for-byte against Marlin's dwin_api.cpp."""

from dwinui import protocol as p

T = bytes.fromhex("cc33c33c")


def test_rgb565():
    assert p.rgb(255, 255, 255) == 0xFFFF
    assert p.rgb(255, 0, 0) == 0xF800
    assert p.rgb(0, 255, 0) == 0x07E0
    assert p.rgb565_to_rgb(0xFFFF) == (255, 255, 255)


def test_system_frames():
    assert p.handshake() == b"\xaa\x00" + T
    assert p.set_dir(1) == bytes.fromhex("aa345aa501") + T
    assert p.update() == b"\xaa\x3d" + T
    assert p.brightness(300) == b"\xaa\x30\xff" + T
    assert p.clear(0x1234) == bytes.fromhex("aa011234") + T


def test_shapes():
    assert p.rect(p.RECT_FILL, 0xFFFF, 1, 2, 3, 4) == \
        bytes.fromhex("aa0501ffff0001000200030004") + T
    assert p.line(0x0001, 10, 20, 30, 40) == bytes.fromhex("aa030001000a0014001e0028") + T


def test_text():
    assert p.text(10, 20, "Hi", font=2, fg=0xFFFF, bg=0x0000) == \
        bytes.fromhex("aa1142ffff0000000a0014") + b"Hi" + T
    no_bg = p.text(0, 0, "A", font=1, show_bg=False)
    assert no_bg[2] == 0x01


def test_text_clipped_to_right_edge():
    frame = p.text(p.WIDTH - 25, 0, "abcdef", font=2)   # room for 2 chars of 10px
    assert frame[11:-4] == b"ab"


def test_symbols_keep_field_width():
    try:
        assert p.encode_text("200°C ←") == b"200 C <"       # stock font fallbacks
        assert p.encode_text("ü") == b"?"
        p.set_glyphs({"°": 0x01, "←": 0x02})
        assert p.encode_text("200°C ←") == b"200\x01C \x02"  # custom font slots
    finally:
        p.set_glyphs({})
    for ch in p.FALLBACK:
        assert len(p.encode_text(ch)) == 1


def test_images():
    assert p.jpg(3) == bytes.fromhex("aa220003") + T
    assert p.icon(9, 5, 10, 20) == bytes.fromhex("aa23000a00148905") + T


def test_parser_handles_garbage_and_chunks():
    parser = p.FrameParser()
    stream = b"\x01\x02" + p.handshake() + b"\xaa\x00OK" + T + bytes.fromhex("aaee01ff") + T
    out = []
    for i in range(0, len(stream), 3):
        out += parser.feed(stream[i:i + 3])
    assert out == [b"\x00", b"\x00OK", b"\xee\x01\xff"]
    assert p.is_handshake_reply(out[1])


def test_input_events():
    assert p.parse_input(b"\xee\x01\xfe") == p.Rotate(-2)
    assert p.parse_input(b"\xee\x01\x03") == p.Rotate(3)
    assert p.parse_input(b"\xee\x02\x01") == p.Button(True)
    assert p.parse_input(b"\xee\x02\x00") == p.Button(False)
    assert p.parse_input(b"\x00OK") is None


def test_bridge_beep():
    assert p.bridge_beep(300) == bytes.fromhex("aaee10012c") + T


def test_points_and_area_move():
    assert p.points(0xF800, [(1, 2), (3, 4)]) == bytes.fromhex("aa02f80001010001000200030004") + T
    many = p.points(0, [(i, i) for i in range(45)])
    assert many.count(T) == 3                      # 20 + 20 + 5 points
    assert p.area_move(p.MOVE_LEFT, 1, 0x1234, 10, 20, 30, 40) == \
        bytes.fromhex("aa09800001" "1234" "000a0014001e0028") + T
