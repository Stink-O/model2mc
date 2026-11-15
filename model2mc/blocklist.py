"""The blocks a model may be built from.

Only full, opaque, solid cubes that stay put and do nothing: no gravity (sand, gravel, concrete powder), no block
entities, no light sources, no redstone, nothing that melts, spreads, grows, oxidises or is see-through, and no ores.
Each entry is (block id, block state properties or "", group). Pillars are placed upright (axis=y).

The colours for these live in blocks.json (measured averages, see palette.measure); this file is only the list.
"""

COLOURS16 = ["white", "light_gray", "gray", "black", "brown", "red", "orange", "yellow", "lime", "green", "cyan",
             "light_blue", "blue", "purple", "magenta", "pink"]

AXIS_Y = "axis=y"

BLOCKS: list[tuple[str, str, str]] = (
    [(f"{c}_concrete", "", "concrete") for c in COLOURS16]
    + [(f"{c}_wool", "", "wool") for c in COLOURS16]
    + [("terracotta", "", "terracotta")]
    + [(f"{c}_terracotta", "", "terracotta") for c in COLOURS16]
    + [(f"{w}_planks", "", "wood") for w in
       ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak", "bamboo",
        "crimson", "warped"]]
    + [(f"stripped_{w}_wood", AXIS_Y, "wood") for w in
       ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak"]]
    + [(f"{w}_wood", AXIS_Y, "wood") for w in
       ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak"]]
    + [("stripped_crimson_hyphae", AXIS_Y, "wood"), ("stripped_warped_hyphae", AXIS_Y, "wood"),
       ("crimson_hyphae", AXIS_Y, "wood"), ("warped_hyphae", AXIS_Y, "wood")]
    + [(b, "", "stone") for b in
       ["stone", "smooth_stone", "stone_bricks", "cobblestone", "mossy_cobblestone", "mossy_stone_bricks",
        "andesite", "polished_andesite", "diorite", "polished_diorite", "granite", "polished_granite",
        "polished_deepslate", "deepslate_bricks", "deepslate_tiles", "cobbled_deepslate", "tuff", "polished_tuff",
        "tuff_bricks", "calcite", "dripstone_block", "blackstone", "polished_blackstone",
        "polished_blackstone_bricks", "smooth_basalt", "bricks", "mud_bricks", "packed_mud",
        "sandstone", "smooth_sandstone", "cut_sandstone", "red_sandstone", "smooth_red_sandstone",
        "cut_red_sandstone", "prismarine", "prismarine_bricks", "dark_prismarine", "end_stone", "end_stone_bricks",
        "purpur_block", "quartz_block", "smooth_quartz", "quartz_bricks", "nether_bricks", "red_nether_bricks",
        "netherrack", "obsidian", "resin_bricks"]]
    + [("deepslate", AXIS_Y, "stone"), ("basalt", AXIS_Y, "stone"), ("polished_basalt", AXIS_Y, "stone"),
       ("quartz_pillar", AXIS_Y, "stone"), ("purpur_pillar", AXIS_Y, "stone")]
    + [(b, "", "natural") for b in
       ["clay", "snow_block", "packed_ice", "blue_ice", "moss_block", "pale_moss_block", "honeycomb_block",
        "dried_kelp_block", "nether_wart_block", "warped_wart_block", "resin_block", "amethyst_block"]]
    + [("hay_block", AXIS_Y, "natural"), ("bone_block", AXIS_Y, "natural")]
    + [(b, "", "metal") for b in
       ["iron_block", "gold_block", "raw_iron_block", "raw_gold_block", "raw_copper_block", "coal_block",
        "lapis_block", "emerald_block", "diamond_block", "netherite_block",
        "waxed_copper_block", "waxed_exposed_copper", "waxed_weathered_copper", "waxed_oxidized_copper",
        "waxed_cut_copper", "waxed_exposed_cut_copper", "waxed_weathered_cut_copper", "waxed_oxidized_cut_copper"]]
)

GROUPS = sorted({g for _, _, g in BLOCKS})

# Blocks that stand out as valuable or odd in a build; left out unless asked for.
OPT_IN = {"gold_block", "diamond_block", "emerald_block", "netherite_block", "lapis_block", "iron_block",
          "raw_gold_block", "amethyst_block"}
