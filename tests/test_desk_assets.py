from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_context_agent_loads_its_built_stylesheet():
    path = Path(__file__).parents[1] / "frappe_app" / "dsherp_bridge" / "hooks.py"
    spec = spec_from_file_location("dsherp_bridge_hooks", path)
    hooks = module_from_spec(spec)
    spec.loader.exec_module(hooks)

    assert hooks.app_include_css == ["/assets/dsherp_bridge/dist/context-agent.css"]


def test_agent_audit_report_defines_required_date_filters():
    report_js = (
        Path(__file__).parents[1]
        / "frappe_app"
        / "dsherp_bridge"
        / "dsherp_bridge"
        / "report"
        / "ds_agent_audit"
        / "ds_agent_audit.js"
    )

    source = report_js.read_text()
    assert 'fieldname: "from_date"' in source
    assert 'fieldname: "to_date"' in source
    assert source.count("reqd: 1") == 2
