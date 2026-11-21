# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from core.rgss_saves import (
    FORMAT_VX,
    FORMAT_VXACE,
    FORMAT_XP,
    load_rgss_names,
    load_save,
    write_save,
)
from core.rpgm_saves import JsonDict
from core.ruby_marshal import StringNode, assign_ids, load_all

RUBY = shutil.which("ruby")
FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "rgss"

pytestmark = pytest.mark.skipif(RUBY is None, reason="ruby not on PATH")

_RUBY_STUBS = r"""
require "json"

class Tone
  attr_reader :raw
  def initialize(raw); @raw = raw; end
  def self._load(s); new(s); end
end

class Color
  attr_reader :raw
  def initialize(raw); @raw = raw; end
  def self._load(s); new(s); end
end

class Table
  attr_reader :raw
  def initialize(raw); @raw = raw; end
  def self._load(s); new(s); end
end

module RPG
  class AudioFile; end
  class BGM < AudioFile; end
  class BGS < AudioFile; end
  class ME < AudioFile; end
end

class Game_System; end
class Game_Timer; end
class Game_Message; end
class Game_Switches; end
class Game_Variables; end
class Game_SelfSwitches; end
class Game_Screen; end
class Game_Actor; end
class Game_Actors; end
class Game_Party; end
class Game_Troop; end
class Game_Map; end
class Game_Player; end
class CustomQuestTracker; end

class Game_Interpreter
  attr_reader :dumped
  def marshal_load(arr)
    @dumped = arr
  end
end

def load_all_dumps(path)
  dumps = []
  File.open(path, "rb") do |io|
    dumps << Marshal.load(io) until io.eof?
  end
  dumps
end
"""


def _run_ruby(script: str, save_path: Path) -> dict[str, object]:
    assert RUBY is not None
    proc = subprocess.run(
        [RUBY, "-e", _RUBY_STUBS + "\n" + script, str(save_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, object], json.loads(proc.stdout))


def test_ruby_reloads_edited_xp_save(tmp_path: Path) -> None:
    save = tmp_path / "Save1.rxdata"
    _ = shutil.copy(FIXTURES_ROOT / "xp" / "Save1.rxdata", save)
    names = load_rgss_names(str(FIXTURES_ROOT / "xp"), FORMAT_XP)
    loaded = load_save(str(save), names)
    gold_ref = cast(int, cast(JsonDict, loaded["view"])["gold_ref"])

    _ = write_save(
        str(save),
        [
            {"op": "set_node", "node": gold_ref, "value": 8888},
            {"op": "set_count", "kind": "weapon", "id": 2, "count": 3},
            {"op": "set_actor", "id": 1, "field": "level", "value": 7},
            {"op": "set_actor", "id": 1, "field": "mp", "value": 200},
            {"op": "set_actor", "id": 1, "field": "name", "value": "Clärís"},
            {"op": "set_switch", "id": 5, "value": True},
            {"op": "set_variable", "id": 10, "value": 42},
        ],
        cast(int, loaded["size"]),
        int(cast(str, loaded["mtime_ns"])),
        lambda: "b-xp",
    )

    data = _run_ruby(
        r"""
dumps = load_all_dumps(ARGV[0])
sys = dumps[2]
sw = dumps[3].instance_variable_get(:@data)
vr = dumps[4].instance_variable_get(:@data)
actor1 = dumps[7].instance_variable_get(:@data)[1]
party = dumps[8]
tracker = sys.instance_variable_get(:@tracker)
puts JSON.generate({
  count: dumps.length,
  gold: party.instance_variable_get(:@gold),
  weapon2: party.instance_variable_get(:@weapons)[2],
  level: actor1.instance_variable_get(:@level),
  exp: actor1.instance_variable_get(:@exp),
  sp: actor1.instance_variable_get(:@sp),
  name: actor1.instance_variable_get(:@name).dup.force_encoding("UTF-8"),
  name_enc: actor1.instance_variable_get(:@name).encoding.name,
  sw5: sw[5],
  vr10: vr[10],
  vr6_nil: vr[6].nil?,
  shared_tone: sys.instance_variable_get(:@tone_a).equal?(sys.instance_variable_get(:@tone_b)),
  cycle_ok: tracker.instance_variable_get(:@partner).equal?(tracker),
})
""",
        save,
    )
    assert data == {
        "count": 12,
        "gold": 8888,
        "weapon2": 3,
        "level": 7,
        "exp": 2100,
        "sp": 200,
        "name": "Clärís",
        "name_enc": "ASCII-8BIT",
        "sw5": True,
        "vr10": 42,
        "vr6_nil": True,
        "shared_tone": True,
        "cycle_ok": True,
    }


def test_ruby_reloads_edited_vx_save(tmp_path: Path) -> None:
    save = tmp_path / "Save1.rvdata"
    _ = shutil.copy(FIXTURES_ROOT / "vx" / "Save1.rvdata", save)
    names = load_rgss_names(str(FIXTURES_ROOT / "vx"), FORMAT_VX)
    loaded = load_save(str(save), names)
    gold_ref = cast(int, cast(JsonDict, loaded["view"])["gold_ref"])

    _ = write_save(
        str(save),
        [
            {"op": "set_node", "node": gold_ref, "value": 12345},
            {"op": "set_count", "kind": "item", "id": 1, "count": 0},
            {"op": "set_count", "kind": "item", "id": 2, "count": 4},
            {"op": "set_actor", "id": 1, "field": "level", "value": 8},
            {"op": "set_actor", "id": 1, "field": "paramPlus", "index": 2, "value": 19},
            {"op": "set_switch", "id": 1, "value": True},
        ],
        cast(int, loaded["size"]),
        int(cast(str, loaded["mtime_ns"])),
        lambda: "b-vx",
    )

    data = _run_ruby(
        r"""
dumps = load_all_dumps(ARGV[0])
sys = dumps[4]
sw = dumps[6].instance_variable_get(:@data)
actor1 = dumps[9].instance_variable_get(:@data)[1]
party = dumps[10]
puts JSON.generate({
  count: dumps.length,
  gold: party.instance_variable_get(:@gold),
  has_item1: party.instance_variable_get(:@items).key?(1),
  item2: party.instance_variable_get(:@items)[2],
  level: actor1.instance_variable_get(:@level),
  exp: actor1.instance_variable_get(:@exp),
  atk_plus: actor1.instance_variable_get(:@atk_plus),
  sw1: sw[1],
  shared_color: sys.instance_variable_get(:@color_a).equal?(sys.instance_variable_get(:@color_b)),
})
""",
        save,
    )
    assert data == {
        "count": 14,
        "gold": 12345,
        "has_item1": False,
        "item2": 4,
        "level": 8,
        "exp": 1863,
        "atk_plus": 19,
        "sw1": True,
        "shared_color": True,
    }


def test_ruby_reloads_edited_vxace_save(tmp_path: Path) -> None:
    save = tmp_path / "Save01.rvdata2"
    _ = shutil.copy(FIXTURES_ROOT / "vxace" / "Save01.rvdata2", save)
    names = load_rgss_names(str(FIXTURES_ROOT / "vxace"), FORMAT_VXACE)
    loaded = load_save(str(save), names)
    gold_ref = cast(int, cast(JsonDict, loaded["view"])["gold_ref"])

    # Also find @text_history[0] node id for a Raw edit
    dumps = load_all(save.read_bytes())
    nodes, _ = assign_ids(dumps)
    hist_id = next(
        nid
        for nid, n in nodes.items()
        if isinstance(n, StringNode) and n.data == b"Welcome to the tower."
    )

    _ = write_save(
        str(save),
        [
            {"op": "set_node", "node": gold_ref, "value": 50000},
            {"op": "set_node", "node": hist_id, "value": "Edited in raw view"},
            {"op": "set_actor", "id": 1, "field": "name", "value": "Clärís"},
            {"op": "set_actor", "id": 1, "field": "level", "value": 12},
            {"op": "set_actor", "id": 2, "field": "paramPlus", "index": 2, "value": 50},
            {"op": "set_switch", "id": 12, "value": True},
            {"op": "set_variable", "id": 25, "value": 3},
        ],
        cast(int, loaded["size"]),
        int(cast(str, loaded["mtime_ns"])),
        lambda: "b-vxace",
    )

    data = _run_ruby(
        r"""
dumps = load_all_dumps(ARGV[0])
header, contents = dumps
sys = contents[:system]
party = contents[:party]
actors = contents[:actors].instance_variable_get(:@data)
sw = contents[:switches].instance_variable_get(:@data)
vr = contents[:variables].instance_variable_get(:@data)
tracker = sys.instance_variable_get(:@tracker)
puts JSON.generate({
  count: dumps.length,
  playtime: header[:playtime_s],
  gold: party.instance_variable_get(:@gold),
  text_history0: party.instance_variable_get(:@text_history)[0],
  a1_name: actors[1].instance_variable_get(:@name),
  a1_name_enc: actors[1].instance_variable_get(:@name).encoding.name,
  a1_level: actors[1].instance_variable_get(:@level),
  a1_exp: actors[1].instance_variable_get(:@exp)[1],
  a2_bonus2: actors[2].instance_variable_get(:@param_plus)[2],
  sw12: sw[12],
  vr25: vr[25],
  vr24_nil: vr[24].nil?,
  shared_tone: sys.instance_variable_get(:@window_tone).equal?(sys.instance_variable_get(:@backup_tone)),
  cycle_ok: tracker.instance_variable_get(:@partner).equal?(tracker),
})
""",
        save,
    )
    assert data == {
        "count": 2,
        "playtime": "00:00:23",
        "gold": 50000,
        "text_history0": "Edited in raw view",
        "a1_name": "Clärís",
        "a1_name_enc": "UTF-8",
        "a1_level": 12,
        "a1_exp": 1250,
        "a2_bonus2": 50,
        "sw12": True,
        "vr25": 3,
        "vr24_nil": True,
        "shared_tone": True,
        "cycle_ok": True,
    }
