"""The Agent page keeps its settings entry in the native Frappe header."""
from pathlib import Path

SOURCE = (Path(__file__).parents[1] / "frappe_app" / "dsherp_bridge" / "dsherp_bridge"
          / "page" / "dsherp_agent" / "dsherp_agent.js").read_text()


def test_settings_use_the_native_page_button_bound_to_the_mount_handle():
    assert 'page.set_secondary_action("Agent 设置"' in SOURCE
    assert '"setting-gear"' in SOURCE
    assert "dispose.openSettings()" in SOURCE


def test_settings_entry_does_not_reveal_an_empty_overflow_menu():
    # Page.add_button also files a hidden-xl twin under the "..." menu, which
    # unhides that menu while showing nothing inside it on desktop.
    assert "page.add_button(" not in SOURCE
    assert "add_menu_item" not in SOURCE


def test_page_does_not_render_a_second_in_app_toolbar_title():
    # The Frappe page head already prints the title; the app must not repeat it.
    assert SOURCE.count('title: "Agent 工作台"') == 1
