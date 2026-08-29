from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_context_agent_loads_its_built_stylesheet():
    path = Path(__file__).parents[1] / "frappe_app" / "dsherp_bridge" / "hooks.py"
    spec = spec_from_file_location("dsherp_bridge_hooks", path)
    hooks = module_from_spec(spec)
    spec.loader.exec_module(hooks)

    assert hooks.app_include_css == ["/assets/dsherp_bridge/dist/context-agent.css"]
