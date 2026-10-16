"""Home tile actions against the Streamlit pages (archived 2026-10-17 with the prototype; the Django pages take the tile's query, see core.menu.TILE_QUERY)."""
import re
from pathlib import Path

from engines.home_tiles import TILES

ROOT = Path(__file__).resolve().parents[3]


def _page_files() -> dict[str, str]:
    """{page id: views file text}, read from app.py's page list so nothing has to run."""
    text = (ROOT / "archive" / "streamlit" / "app.py").read_text(encoding="utf-8")
    block = text[text.index("PAGES.update({"):text.index("})", text.index("PAGES.update({"))]
    out = {}
    for pid, mod in re.findall(r'"([a-z-]+)": st\.Page\(views\.(\w+)\.render', block):
        out[pid] = (ROOT / "archive" / "streamlit" / "views" / f"{mod}.py").read_text(encoding="utf-8")
    return out


def test_every_action_opens_a_real_page_and_sets_a_widget_that_page_really_has():
    files = _page_files()
    for t in TILES:
        for a in t.actions:
            target = a.page_id or t.page_id
            assert target in files, (t.page_id, a.label)
            for key, _ in a.state:
                assert f'"{key}"' in files[target], f"{t.title} / {a.label}: widget key {key} is not on the {target} page"


def test_the_mode_values_the_actions_set_are_the_pages_own_constants_and_labels():
    from views import shared
    from views.add_data import KINDS
    from views.reports import REPORT_OPTIONS
    for t in TILES:
        for a in t.actions:
            st_ = dict(a.state)
            if "ad_kind" in st_:
                assert st_["ad_kind"] in KINDS
            if "report_type_picker" in st_:
                assert st_["report_type_picker"] in REPORT_OPTIONS
    assert {"Browse batches", "New batch"} == {shared.OS_BROWSE, shared.OS_NEW}
    assert {"Browse loan notes", "New loan note"} == {shared.LN_BROWSE, shared.LN_NEW}
    assert {"Browse bank loans", "New bank loan"} == {shared.BL_BROWSE, shared.BL_NEW}
