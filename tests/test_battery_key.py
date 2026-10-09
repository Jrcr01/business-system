"""Batteries on the standard price sheet (owner, 2026-10-09: "the problem is still here"): the line (MF Plus, Excel) is in Model/Pattern and the size/group code
(NS40, 1SMF) in Size -- both name the battery, so 'Ultra MF Plus NS40' and 'Ultra MF Plus NS60' are two products, never 'the same product, another price'."""
from engines.product_catalog import product_key, split_other_specs, standard_name


def _key(model, size, brand="Ultra"):
    vals, rest = split_other_specs("Battery", model, size, "")
    return product_key("Battery", brand, rest, **vals), standard_name("Battery", brand, **vals)


def test_the_line_and_the_size_code_both_name_the_battery():
    a, b, c = _key("MF Plus", "NS40"), _key("MF Plus", "NS60"), _key("LM Plus", "NS40")
    assert len({a[0], b[0], c[0]}) == 3
    assert a[1] == "Ultra MF Plus NS40"
    assert _key("NS40", "")[0] == _key("", "NS40")[0] == _key("NS40", "NS40")[0]      # the older ways of filling it still give one battery

