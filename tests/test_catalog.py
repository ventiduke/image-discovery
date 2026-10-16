from pathlib import Path

import pytest

from imagefind.catalog import CatalogError, load, parse

CONTENT = Path(__file__).parent.parent / "content"


def loc(text, **others):
    return {"defaultText": text, "localizedByCulture": {"en-US": text, **others}}


SAMPLE = {
    "version": "1.2.3",
    "weapons": [
        {"id": "w1", "label": "w1", "name": loc("Vandal", **{"de-DE": "Vandal", "ja-JP": "ヴァンダル"}),
         "category": "Rifle", "stats": {"fireRate": 9.75}},
    ],
    "weaponSkins": [
        {"id": "s1", "label": "s1", "name": loc("Reaver Vandal"), "weaponId": "w1",
         "chromas": [{"id": "c1", "label": "c1", "name": loc("Reaver Vandal Red")}],
         "levels": [{"id": "l1", "label": "l1", "name": loc("Reaver Vandal Level 2")}]},
    ],
    "characters": [
        {"id": "ch1", "label": "ch1", "name": loc("Gekko"), "description": loc("Leads a crew."),
         "abilities": {
             "ability1": {"label": "ch1_Ability1", "name": loc("Wingman"), "description": loc("")},
             "ultimate": {"label": "ch1_Ultimate", "name": loc("Thrash")},
         }},
    ],
    "playerTitles": [{"id": "t1", "label": "t1", "name": loc("Watchdog Title"), "titleText": loc("Watchdog")}],
    "maps": [{"id": "m1", "label": "m1", "name": loc("Ascent"), "coordinates": loc("")}],
}


def test_flattens_sections_and_nested_items():
    cat = parse(SAMPLE)
    assert cat.version == "1.2.3"
    kinds = {e.key: e.kind for e in cat.entries.values()}
    assert kinds == {
        "W1": "weapons", "S1": "weaponSkins", "C1": "weaponSkins.chromas",
        "L1": "weaponSkins.levels", "CH1": "characters", "CH1_ABILITY1": "characters.abilities",
        "CH1_ULTIMATE": "characters.abilities", "T1": "playerTitles", "M1": "maps",
    }
    assert cat.entries["C1"].parent == "S1"
    assert cat.entries["CH1_ABILITY1"].parent == "CH1"
    assert cat.entries["CH1_ABILITY1"].section == "characters"


def test_abilities_have_no_id_and_are_found_by_label():
    cat = parse(SAMPLE)
    ability = cat.find("ch1_ability1")
    assert ability.name == "Wingman" and ability.id is None and ability.label == "ch1_Ability1"


def test_english_names_other_languages_and_fields():
    cat = parse(SAMPLE)
    vandal = cat.find("W1")
    assert vandal.name == "Vandal"
    assert vandal.names == {"ja-JP": "ヴァンダル"}  # same-as-English names are left out
    assert vandal.fields == {"category": "Rifle"}  # nested stats stay in raw only
    assert vandal.raw["stats"] == {"fireRate": 9.75}
    assert cat.find("s1").fields == {"weaponId": "w1"}
    assert "chromas" not in cat.find("s1").raw  # nested entries aren't duplicated in raw
    assert cat.find("t1").fields == {"titleText": "Watchdog"}
    assert cat.find("m1").fields == {}  # empty localized text is dropped
    assert cat.find("ch1").description == "Leads a crew."


def test_falls_back_to_en_us_when_default_text_is_empty():
    cat = parse({"maps": [{"id": "m", "name": {"defaultText": "", "localizedByCulture": {"en-US": "Bind"}}}]})
    assert cat.find("m").name == "Bind"


def test_ignores_non_entries_and_duplicates():
    cat = parse({"version": "1", "maps": [{"id": "m", "name": loc("A")}, {"id": "M", "name": loc("B")}, 5, {}]})
    assert [e.name for e in cat.entries.values()] == ["A"]


def test_real_catalog():
    cat = load(CONTENT / "PublicContentCatalog.json")
    assert cat.version == "13.06.00.5435758"
    assert cat.find("E370FA57-4757-3604-3648-499E1F642D3F_Ability1").name == "Wingman"
    kinds = {}
    for e in cat.entries.values():
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    assert kinds["characters.abilities"] == 121
    assert kinds["weaponSkins.chromas"] == 2931


def test_unreadable_catalog(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text("{not json")
    with pytest.raises(CatalogError, match="Can't read c.json"):
        load(bad)
    bad.write_text("[]")
    with pytest.raises(CatalogError, match="isn't a catalog"):
        load(bad)
