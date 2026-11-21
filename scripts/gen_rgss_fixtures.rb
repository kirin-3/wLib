#!/usr/bin/env ruby
# frozen_string_literal: true
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Generates small, deterministic RPG Maker XP, VX and VX Ace Marshal fixtures
# under tests/fixtures/rgss/{xp,vx,vxace}/. Commit the generated files so CI
# does not need Ruby.

require "fileutils"

ROOT = File.expand_path("../tests/fixtures/rgss", __dir__)

# ------------------------------------------------------------- RGSS primitives

class Tone
  def initialize(red = 0.0, green = 0.0, blue = 0.0, gray = 0.0)
    @red = red.to_f
    @green = green.to_f
    @blue = blue.to_f
    @gray = gray.to_f
  end

  def _dump(_limit)
    [@red, @green, @blue, @gray].pack("E4")
  end
end

class Color
  def initialize(red = 0.0, green = 0.0, blue = 0.0, alpha = 255.0)
    @red = red.to_f
    @green = green.to_f
    @blue = blue.to_f
    @alpha = alpha.to_f
  end

  def _dump(_limit)
    [@red, @green, @blue, @alpha].pack("E4")
  end
end

class Table
  def initialize(xsize, ysize = 1, zsize = 1, dims = 1, data = nil)
    @dims = dims
    @xsize = xsize
    @ysize = ysize
    @zsize = zsize
    total = xsize * ysize * zsize
    @data = data || Array.new(total, 0)
  end

  def _dump(_limit)
    [@dims, @xsize, @ysize, @zsize, @data.length, *@data].pack("V5v*")
  end
end

# ------------------------------------------------------------------ RPG module

module RPG
  class AudioFile
    def initialize(name = "", volume = 100, pitch = 100)
      @name = name
      @volume = volume
      @pitch = pitch
    end
  end

  class BGM < AudioFile; end
  class BGS < AudioFile; end
  class ME < AudioFile; end

  class Item
    def initialize(id:, name:, description: "", itype_id: nil)
      @id = id
      @name = name
      @description = description
      @itype_id = itype_id unless itype_id.nil?
    end
  end

  class Weapon
    def initialize(id:, name:, description: "")
      @id = id
      @name = name
      @description = description
    end
  end

  class Armor
    def initialize(id:, name:, description: "")
      @id = id
      @name = name
      @description = description
    end
  end

  class Actor
    def initialize(id:, name:)
      @id = id
      @name = name
    end
  end

  class System
    class Words
      def initialize(gold:, hp:, sp:, str:, dex:, agi:, int:)
        @gold = gold
        @hp = hp
        @sp = sp
        @str = str
        @dex = dex
        @agi = agi
        @int = int
      end
    end

    class Terms
      def initialize(gold: nil, hp: nil, mp: nil, atk: nil, defense: nil, spi: nil, agi: nil, params: nil)
        @gold = gold unless gold.nil?
        @hp = hp unless hp.nil?
        @mp = mp unless mp.nil?
        @atk = atk unless atk.nil?
        @def = defense unless defense.nil?
        @spi = spi unless spi.nil?
        @agi = agi unless agi.nil?
        @params = params unless params.nil?
      end
    end

    def initialize(switches:, variables:, words: nil, terms: nil, currency_unit: nil)
      @switches = switches
      @variables = variables
      @words = words unless words.nil?
      @terms = terms unless terms.nil?
      @currency_unit = currency_unit unless currency_unit.nil?
    end
  end
end

# ---------------------------------------------------------------- Game classes

class Game_System
  def initialize(**ivars)
    ivars.each { |k, v| instance_variable_set(:"@#{k}", v) }
  end
end

class Game_Timer
  def initialize(count = 0, working = false)
    @count = count
    @working = working
  end
end

class Game_Message
  def initialize(texts = [])
    @texts = texts
  end
end

class Game_Switches
  def initialize(data)
    @data = data
  end
end

class Game_Variables
  def initialize(data)
    @data = data
  end
end

class Game_SelfSwitches
  def initialize(data)
    @data = data
  end
end

class Game_Screen
  def initialize(tone:, flash_color:)
    @tone = tone
    @flash_color = flash_color
  end
end

class Game_Actor
  def initialize(**ivars)
    ivars.each { |k, v| instance_variable_set(:"@#{k}", v) }
  end
end

class Game_Actors
  def initialize(data)
    @data = data
  end
end

class Game_Party
  def initialize(**ivars)
    ivars.each { |k, v| instance_variable_set(:"@#{k}", v) }
  end
end

class Game_Troop
  def initialize(turn_count = 0)
    @turn_count = turn_count
  end
end

class Game_Interpreter
  def initialize(depth: 0, map_id: 1, event_id: 0, index: 0)
    @depth = depth
    @map_id = map_id
    @event_id = event_id
    @list = nil
    @index = index
    @branch = {}
  end

  def marshal_dump
    [@depth, @map_id, @event_id, @list, @index, @branch]
  end
end

class Game_Map
  def initialize(**ivars)
    ivars.each { |k, v| instance_variable_set(:"@#{k}", v) }
  end
end

class Game_Player
  def initialize(x = 3, y = 5)
    @x = x
    @y = y
  end
end

# Script-defined class (not part of standard RGSS)
class CustomQuestTracker
  attr_accessor :partner

  def initialize(tag:, score:, note:, raw_bytes:, sjis_note:)
    @tag = tag
    @score = score
    @note = note
    @raw_bytes = raw_bytes
    @sjis_note = sjis_note
    @partner = nil
  end
end

# --------------------------------------------------------------------- helpers

def raw_str(text)
  # ASCII-8BIT string with UTF-8 bytes and no encoding ivar (like Ruby 1.8 / RGSS1 & RGSS2)
  text.dup.force_encoding(Encoding::ASCII_8BIT)
end

def write_dumps(path, dumps)
  FileUtils.mkdir_p(File.dirname(path))
  File.open(path, "wb") do |io|
    dumps.each { |obj| Marshal.dump(obj, io) }
  end
end

def write_single(path, obj)
  write_dumps(path, [obj])
end

# ------------------------------------------------------------------ XP fixture

def build_xp
  dir = File.join(ROOT, "xp")
  data_dir = File.join(dir, "Data")

  # Data files (.rxdata)
  write_single(File.join(data_dir, "Items.rxdata"), [
    nil,
    RPG::Item.new(id: 1, name: raw_str('\C[2]Herb\C[0]'), description: raw_str("Restores HP.")),
    RPG::Item.new(id: 2, name: raw_str("Elixir"), description: raw_str("Restores HP and SP.")),
    RPG::Item.new(id: 3, name: raw_str(""), description: raw_str(""))
  ])
  write_single(File.join(data_dir, "Weapons.rxdata"), [
    nil,
    RPG::Weapon.new(id: 1, name: raw_str("Bronze Sword"), description: raw_str("A basic sword.")),
    RPG::Weapon.new(id: 2, name: raw_str("Iron Sword"), description: raw_str("A sturdy sword."))
  ])
  write_single(File.join(data_dir, "Armors.rxdata"), [
    nil,
    RPG::Armor.new(id: 1, name: raw_str("Cloth Tunic"), description: raw_str("Simple cloth.")),
    RPG::Armor.new(id: 2, name: raw_str("Bronze Shield"), description: raw_str("Light shield."))
  ])
  write_single(File.join(data_dir, "Actors.rxdata"), [
    nil,
    RPG::Actor.new(id: 1, name: raw_str("Aluxes")),
    RPG::Actor.new(id: 2, name: raw_str("Basil"))
  ])
  write_single(File.join(data_dir, "System.rxdata"), RPG::System.new(
    switches: [nil, raw_str("Intro Done"), raw_str("Bridge Open"), raw_str('\C[1]Boss Defeated\C[0]')],
    variables: [nil, raw_str("Kills"), raw_str("Hero Title"), raw_str("Combo Rate"), raw_str("Unset Var")],
    words: RPG::System::Words.new(
      gold: raw_str("Gold"),
      hp: raw_str("HP"),
      sp: raw_str("SP"),
      str: raw_str("STR"),
      dex: raw_str("DEX"),
      agi: raw_str("AGI"),
      int: raw_str("INT")
    )
  ))

  # Save1.rxdata: 12 dumps
  shared_tone = Tone.new(10.0, -5.0, 0.0, 20.0)
  node_a = CustomQuestTracker.new(
    tag: :xp_quest,
    score: (1 << 70) + 12345,
    note: "UTF-8 café".encode(Encoding::UTF_8),
    raw_bytes: "\xFF\xFE\x00\x01".b,
    sjis_note: "テスト".encode(Encoding::Shift_JIS)
  )
  node_a.partner = node_a # cycle

  self_switches = Hash.new(false)
  self_switches[[1, 2, raw_str("A")]] = true

  actor1 = Game_Actor.new(
    actor_id: 1,
    name: raw_str("Aluxes"),
    class_id: 1,
    level: 5,
    exp: 1000,
    exp_list: [0, 0, 100, 300, 600, 1000, 1500, 2100, 2800],
    hp: 420,
    sp: 110,
    maxhp_plus: 20,
    maxsp_plus: 10,
    str_plus: 5,
    dex_plus: 3,
    agi_plus: -2,
    int_plus: 4
  )
  actor2 = Game_Actor.new(
    actor_id: 2,
    name: raw_str("Basil"),
    class_id: 2,
    level: 3,
    exp: 300,
    exp_list: [0, 0, 100, 300, 600],
    hp: 310,
    sp: 80,
    maxhp_plus: 0,
    maxsp_plus: 0,
    str_plus: 0,
    dex_plus: 0,
    agi_plus: 0,
    int_plus: 0
  )

  system = Game_System.new(
    magic_number: 12345,
    windowskin_name: raw_str("001-Blue01"),
    tone_a: shared_tone,
    tone_b: shared_tone, # shared object within dump 2
    tracker: node_a
  )

  dumps = [
    [[raw_str("001-Fighter01"), 0]],                                  # 0: characters
    144_000,                                                          # 1: frame_count (1h at 40fps)
    system,                                                           # 2: game_system
    Game_Switches.new([nil, true, false]),                            # 3: game_switches
    Game_Variables.new([nil, 7, raw_str("Scout"), 1.5]),              # 4: game_variables
    Game_SelfSwitches.new(self_switches),                             # 5: game_self_switches
    Game_Screen.new(tone: Tone.new(0, 0, 0, 0), flash_color: Color.new(255, 128, 64, 200)), # 6: game_screen
    Game_Actors.new([nil, actor1, actor2]),                           # 7: game_actors
    Game_Party.new(                                                   # 8: game_party
      actors: [actor1],
      gold: 750,
      steps: 42,
      items: { 1 => 4, 2 => 1 },
      weapons: { 1 => 2 },
      armors: { 1 => 1 }
    ),
    Game_Troop.new(0),                                                # 9: game_troop
    Game_Map.new(map_id: 1, data: Table.new(2, 2, 1, 3, [1, 2, 3, 4])), # 10: game_map
    Game_Player.new(4, 6)                                             # 11: game_player
  ]
  write_dumps(File.join(dir, "Save1.rxdata"), dumps)
end

# ------------------------------------------------------------------ VX fixture

def build_vx
  dir = File.join(ROOT, "vx")
  data_dir = File.join(dir, "Data")

  # Data files (.rvdata)
  write_single(File.join(data_dir, "Items.rvdata"), [
    nil,
    RPG::Item.new(id: 1, name: raw_str("Potion"), description: raw_str("Heals 500 HP.")),
    RPG::Item.new(id: 2, name: raw_str('\C[3]Ether\C[0]'), description: raw_str("Heals 100 MP.")),
    RPG::Item.new(id: 3, name: raw_str("   "), description: raw_str(""))
  ])
  write_single(File.join(data_dir, "Weapons.rvdata"), [
    nil,
    RPG::Weapon.new(id: 1, name: raw_str("Short Sword"), description: raw_str("Light blade."))
  ])
  write_single(File.join(data_dir, "Armors.rvdata"), [
    nil,
    RPG::Armor.new(id: 1, name: raw_str("Leather Vest"), description: raw_str("Basic vest."))
  ])
  write_single(File.join(data_dir, "Actors.rvdata"), [
    nil,
    RPG::Actor.new(id: 1, name: raw_str("Sona")),
    RPG::Actor.new(id: 2, name: raw_str("Ralph"))
  ])
  write_single(File.join(data_dir, "System.rvdata"), RPG::System.new(
    switches: [nil, raw_str("Prologue Clear"), raw_str("Gate Unlocked")],
    variables: [nil, raw_str("Score"), raw_str("Rank"), raw_str("Ratio")],
    terms: RPG::System::Terms.new(
      gold: raw_str("G"),
      hp: raw_str("HP"),
      mp: raw_str("MP"),
      atk: raw_str("ATK"),
      defense: raw_str("DEF"),
      spi: raw_str("SPI"),
      agi: raw_str("AGI")
    )
  ))

  # Save1.rvdata: 14 dumps
  shared_color = Color.new(10, 20, 30, 255)
  self_switches = Hash.new(0)
  self_switches[[1, 1, raw_str("B")]] = 1

  actor1 = Game_Actor.new(
    actor_id: 1,
    name: raw_str("Sona"),
    class_id: 1,
    level: 4,
    exp: 420,
    exp_list: [0, 0, 50, 180, 420, 750, 1100, 1480, 1863],
    hp: 250,
    mp: 60,
    maxhp_plus: 15,
    maxmp_plus: 5,
    atk_plus: 3,
    def_plus: 2,
    spi_plus: 1,
    agi_plus: -1
  )

  dumps = [
    [[raw_str("Actor1"), 0]],                                         # 0: characters
    3660,                                                             # 1: frame_count (00:01:01 at 60fps)
    RPG::BGM.new(raw_str("Field1"), 80, 100),                         # 2: last_bgm
    RPG::BGS.new(raw_str(""), 80, 100),                               # 3: last_bgs
    Game_System.new(                                                  # 4: game_system
      save_count: 3,
      color_a: shared_color,
      color_b: shared_color,
      big_counter: -(1 << 66) - 7,
      factor: 0.75
    ),
    Game_Message.new([raw_str("Welcome")]),                           # 5: game_message
    Game_Switches.new([nil, false, true]),                            # 6: game_switches
    Game_Variables.new([nil, 99, raw_str("Silver"), 2.25, :sym_val]), # 7: game_variables
    Game_SelfSwitches.new(self_switches),                             # 8: game_self_switches
    Game_Actors.new([nil, actor1]),                                   # 9: game_actors
    Game_Party.new(                                                   # 10: game_party
      actors: [1],
      gold: 120,
      steps: 18,
      items: { 1 => 5 },
      weapons: { 1 => 1 },
      armors: { 1 => 1 }
    ),
    Game_Troop.new(1),                                                # 11: game_troop
    Game_Map.new(                                                     # 12: game_map
      map_id: 2,
      tone: Tone.new(-10, 0, 10, 0),
      tiles: Table.new(2, 1, 1, 2, [10, 20])
    ),
    Game_Player.new(7, 8)                                             # 13: game_player
  ]
  write_dumps(File.join(dir, "Save1.rvdata"), dumps)
end

# -------------------------------------------------------------- VX Ace fixture

def build_vxace
  dir = File.join(ROOT, "vxace")
  data_dir = File.join(dir, "Data")

  # Data files (.rvdata2)
  write_single(File.join(data_dir, "Items.rvdata2"), [
    nil,
    RPG::Item.new(id: 1, name: "Magic Water", description: "Restores MP.", itype_id: 1),
    RPG::Item.new(id: 2, name: '\C[6]Old Key\C[0]', description: "Opens the tower gate.", itype_id: 2),
    RPG::Item.new(id: 3, name: "Hidden Token", description: "Internal item.", itype_id: 3),
    RPG::Item.new(id: 4, name: "", description: "", itype_id: 1)
  ])
  write_single(File.join(data_dir, "Weapons.rvdata2"), [
    nil,
    RPG::Weapon.new(id: 1, name: "Steel Blade", description: "Sharp steel."),
    RPG::Weapon.new(id: 2, name: "Oak Staff", description: "Mage staff.")
  ])
  write_single(File.join(data_dir, "Armors.rvdata2"), [
    nil,
    RPG::Armor.new(id: 1, name: "Iron Helm", description: "Protects the head."),
    RPG::Armor.new(id: 2, name: "Travel Cloak", description: "Warm cloak.")
  ])
  write_single(File.join(data_dir, "Actors.rvdata2"), [
    nil,
    RPG::Actor.new(id: 1, name: "Eric"),
    RPG::Actor.new(id: 2, name: "Natalie")
  ])
  write_single(File.join(data_dir, "System.rvdata2"), RPG::System.new(
    switches: [nil, "Tutorial Seen", "Tower Opened", "Secret Ending"],
    variables: [nil, "Gold Found", "Companion Name", "Affection", "Raw Object"],
    currency_unit: "GP",
    terms: RPG::System::Terms.new(
      params: ["Max HP", "Max MP", "ATK", "DEF", "MAT", "MDF", "AGI", "LUK"]
    )
  ))

  # Save01.rvdata2: header + contents
  header = {
    characters: [["Actor1", 0], ["Actor2", 1]],
    playtime_s: "00:00:23",
    frame_count: 1380
  }

  shared_obj = Tone.new(5.0, 10.0, 15.0, 0.0)
  tracker = CustomQuestTracker.new(
    tag: :ace_quest,
    score: (1 << 80) + 99,
    note: "Clärís",
    raw_bytes: "\x80\xFF\x00".b,
    sjis_note: "日本語".encode(Encoding::Shift_JIS)
  )
  tracker.partner = tracker # cycle

  self_switches = Hash.new(false)
  self_switches[[1, 3, "A"]] = true

  actor1 = Game_Actor.new(
    actor_id: 1,
    name: "Eric",
    class_id: 1,
    level: 6,
    exp: { 1 => 1250 },
    hp: 500,
    mp: 120,
    param_plus: [25, 10, 4, 2, 0, 0, -3, 5]
  )
  actor2 = Game_Actor.new(
    actor_id: 2,
    name: "Natalie",
    class_id: 2,
    level: 5,
    exp: { 2 => 800 },
    hp: 380,
    mp: 200,
    param_plus: [0, 20, 0, 0, 12, 8, 1, 0]
  )

  contents = {
    system: Game_System.new(
      save_count: 7,
      version_id: 987654,
      window_tone: shared_obj,
      backup_tone: shared_obj, # shared object link
      tracker: tracker
    ),
    timer: Game_Timer.new(0, false),
    message: Game_Message.new([]),
    switches: Game_Switches.new([nil, true, false]),
    variables: Game_Variables.new([nil, 50, "Natalie", 3.5, Tone.new(1, 2, 3, 4)]),
    self_switches: Game_SelfSwitches.new(self_switches),
    actors: Game_Actors.new([nil, actor1, actor2]),
    party: Game_Party.new(
      actors: [1, 2],
      gold: 2500,
      steps: 105,
      items: { 1 => 3, 2 => 1 },
      weapons: { 1 => 1, 42 => 1 }, # weapon 42 missing from database -> Unknown weapon #42
      armors: { 1 => 2 },
      text_history: ["Welcome to the tower."]
    ),
    troop: Game_Troop.new(0),
    map: Game_Map.new(
      map_id: 4,
      interpreter: Game_Interpreter.new(depth: 0, map_id: 4, event_id: 2, index: 1),
      color: Color.new(255, 255, 255, 128),
      table: Table.new(3, 2, 1, 2, [1, 2, 3, 4, 5, 6])
    ),
    player: Game_Player.new(10, 12)
  }

  write_dumps(File.join(dir, "Save01.rvdata2"), [header, contents])
end

build_xp
build_vx
build_vxace
puts "Generated RGSS fixtures in #{ROOT}"
