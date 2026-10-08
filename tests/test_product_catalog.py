"""The product master: standard fields per category, our own SKU, one product however it is spelled, and suppliers' prices for the same product side by side."""
from datetime import date

from conftest import fetchone, make_creditor
from engines import discount_rules as dr
from engines import product_catalog as pc
from engines.price_list_service import add_price


def test_standard_names_per_category():
    assert pc.standard_name("Tires", "Westlake", size="285/50R20", terrain="a/t", pattern="SL369") == "Westlake 285/50R20 (A/T) SL369"
    assert pc.standard_name("Tires", "Westlake", size="LT265/65R17", ply="10PR", terrain="M/T", pattern="SL378") == "Westlake LT265/65R17 10PR (M/T) SL378"
    assert pc.standard_name("Battery", "Motolite", model="NS60", volts="12", ah="45", terminal="L") == "Motolite NS60 12V 45Ah L"
    assert pc.standard_name("Mags", "Enkei", model="RPF1", size="17x7.5", pcd="5x114.3", offset="ET40", finish="Silver") == "Enkei RPF1 17x7.5 5x114.3 ET40 Silver"
    assert pc.standard_name("Rims", "Westlake", size="8.25x22.5", holes="10", tube="TL") == "Westlake 8.25x22.5 10H Tubeless"


def test_one_product_however_it_is_spelled_and_each_gets_its_own_sku(db):
    a = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    b = pc.get_or_create_product("Tires", "WESTLAKE", size="285/50r20", terrain="a/t", pattern="sl 369")
    assert a[2] is True and b[2] is False and a[0] == b[0] and b[1] == "Westlake 285/50R20 (A/T) SL369"          # the first spelling is kept
    c = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", pattern="SA57")
    d = pc.get_or_create_product("Battery", "Motolite", model="NS60", ah="45", terminal="L")
    skus = {pid: fetchone(db, "SELECT code FROM items WHERE id = ?", (pid,))[0] for pid in (a[0], c[0], d[0])}
    assert skus[a[0]] == "TR-0001" and skus[c[0]] == "TR-0002" and skus[d[0]] == "BA-0001"                    # a running number per category
    assert pc.get_or_create_product("Tires", "", size="x")[0] is None and pc.get_or_create_product("Spoons", "X")[0] is None
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'INV_ITEM_ADDED'")[0] == 3


def test_every_spec_is_part_of_the_identity(db):
    """Owner, 2026-10-08: same category + brand + model + size + other specs = the same product (before, type, tube and a battery's Ah did not count)."""
    a = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", pattern="SL369", type="PCR")[0]
    assert pc.get_or_create_product("Tires", "WESTLAKE", size="285/50 r20", pattern="sl-369", type="pcr")[0] == a                  # spaces, dashes, capitals don't
    assert pc.get_or_create_product("Tires", "Westlake", size="285/50R20", pattern="SL369", type="LT", tube="TL")[0] != a
    assert pc.get_or_create_product("Tires", "Westlake", size="285/50R20", pattern="SL369", ply="10PR")[0] != a
    assert pc.get_or_create_product("Battery", "Motolite", model="NS60", ah="45", terminal="L")[0] != pc.get_or_create_product("Battery", "Motolite", model="NS60", ah="60", terminal="L")[0]


def test_the_suppliers_own_code_is_remembered_and_a_blank_never_erases_it(db):
    cid = make_creditor(db)
    pid = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", pattern="SL369")[0]
    pc.set_supplier_code(cid, pid, "WL-2850")
    pc.set_supplier_code(cid, pid, "")
    assert fetchone(db, "SELECT supplier_code FROM supplier_items WHERE creditor_id = ? AND item_id = ?", (cid, pid))[0] == "WL-2850"
    pc.set_supplier_code(cid, pid, "WL-2850B")
    assert fetchone(db, "SELECT COUNT(*), MAX(supplier_code) FROM supplier_items") == (1, "WL-2850B")


def test_two_suppliers_prices_for_the_same_product_sit_side_by_side(db):
    s1, s2 = make_creditor(db, name="New Century"), make_creditor(db, name="Other Trading")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    add_price(s1, name, "pc", 7600, date(2026, 9, 15))
    add_price(s2, name, "pc", 7300, date(2026, 9, 20))
    pc.link_price_rows(s1, name, pid)
    pc.link_price_rows(s2, name, pid)
    dr.set_discount(s1, "Westlake", "5,5", date(2026, 9, 1))                                                        # supplier one gives 5%, 5%
    one = pc.supplier_prices(category="Tires", on=date(2026, 10, 1)).set_index("supplier")
    assert one.loc["New Century", "list_price"] == 7600 and one.loc["New Century", "net_price"] == 6859.0 and one.loc["Other Trading", "net_price"] == 7300.0
    assert bool(one.loc["New Century", "cheapest"]) and not bool(one.loc["Other Trading", "cheapest"])              # net, not list, decides
    assert set(one["sku"]) == {"TR-0001"}


def test_listing_and_discontinuing_products(db):
    pid = pc.get_or_create_product("Tires", "Apollo", size="12R22.5", ply="16PR", pattern="EnduTrax MA")[0]
    assert list(pc.list_products("Tires")["name"]) == ["Apollo 12R22.5 16PR EnduTrax MA"] and pc.brands("Tires") == ["Apollo"]
    assert pc.set_product_active(pid, False)[0] and pc.list_products("Tires").empty and len(pc.list_products("Tires", active_only=False)) == 1
    assert pc.set_product_active(pid, True)[0] and len(pc.list_products("Tires")) == 1
