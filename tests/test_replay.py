import lzma
import struct

from osuprog import replay


def _string(text: str) -> bytes:
    raw = text.encode()
    return b"\x0b" + bytes([len(raw)]) + raw


def make_osr(frames: list[tuple[int, float, float, int]]) -> bytes:
    """Petit .osr valide : frames = (écart en ms depuis l'image précédente, x, y, touches)."""
    body = ",".join(f"{w}|{x}|{y}|{k}" for w, x, y, k in frames) + ",-12345|0|0|0,"
    data = lzma.compress(body.encode(), format=lzma.FORMAT_ALONE)
    head = struct.pack("<Bi", 0, 20240101) + _string("md5") + _string("joueur") + _string("r")
    head += struct.pack("<6H", 2, 0, 0, 0, 0, 1) + struct.pack("<iHBi", 1000, 2, 0, 0) + b"\x00"
    head += struct.pack("<qi", 0, len(data))
    return head + data + struct.pack("<q", 0)


BEATMAP = {"cs": 4.0, "od": 5.0, "objects": [(1000, 100, 100, 0), (2000, 300, 200, 0), (3000, 200, 300, 0)]}


def test_parse_osr_reads_counts_and_absolute_frame_times():
    rp = replay.parse_osr(make_osr([(0, 0, 0, 0), (990, 100, 100, 1), (20, 100, 100, 0)]))
    assert rp.counts["300"] == 2 and rp.counts["miss"] == 1
    assert [f[0] for f in rp.frames] == [0, 990, 1010]


def test_analyse_judges_timing_and_classifies_misses():
    frames = [
        (0, 0, 0, 0),
        (990, 100, 100, 1), (20, 100, 100, 0),      # note 1 : 10 ms en avance, sur la note -> 300
        (1000, 0, 0, 2), (20, 0, 0, 0),             # note 2 : au bon moment mais à côté -> miss de visée
        (1500, 200, 300, 1), (20, 200, 300, 0),     # note 3 : 470 ms trop tard -> miss de rythme
    ]
    a = replay.analyse(replay.parse_osr(make_osr(frames)), BEATMAP, set())
    assert a.great == 1 and a.errors == [-10]
    assert a.miss_aim == 1 and a.miss_timing == 1
    assert a.judged == 3


def test_hard_rock_flips_the_cursor_and_double_time_scales_errors():
    frames = [(0, 0, 0, 0), (1030, 100, 384 - 100, 1), (20, 100, 284, 0)]
    a = replay.analyse(replay.parse_osr(make_osr(frames)), {**BEATMAP, "objects": BEATMAP["objects"][:1]}, {"HR", "DT"})
    assert a.misses == 0 and a.errors == [20.0]  # 30 ms de temps de map = 20 ms réels en DT


def test_presses_only_count_new_key_downs():
    frames = [(0, 0, 0, 1), (10, 0, 0, 1), (20, 0, 0, 3), (30, 0, 0, 0), (40, 0, 0, 2)]
    assert [p[0] for p in replay.presses(frames)] == [0, 20, 40]
