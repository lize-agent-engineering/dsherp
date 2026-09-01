"""Static deployment guards for the atomic v16 runtime switch."""
import json
import plistlib
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
COMPOSE_PATH = ROOT / "infra/compose.validation.yml"
COMPOSE = COMPOSE_PATH.read_text()
ERP_V15 = "cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341"
ERP_V16 = "493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd"
DB_V15 = "92e50059ea0a5965a33ef751970eab37d421b91ebbd01ac909039cffe159e574"
DB_V16 = "2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3"


def test_all_deployment_entrypoints_use_only_the_pinned_v16_images():
    paths = [
        COMPOSE_PATH,
        ROOT / "infra/prepare_agent_runtime.sh",
        ROOT / "dsherp/runtime_host.py",
    ]
    sources = [path.read_text() for path in paths]
    assert not [path.relative_to(ROOT).as_posix() for path, source in zip(paths, sources) if ERP_V15 in source]
    assert sum(source.count(ERP_V16) for source in sources) == 11
    assert DB_V15 not in COMPOSE
    assert COMPOSE.count(DB_V16) == 1


def test_compose_uses_only_fresh_v16_named_volumes():
    expected = {
        "v16-sites",
        "v16-logs",
        "v16-db-data",
        "v16-redis-data",
        "v16-platform-sites",
        "v16-platform-logs",
        "v16-beta-sites",
        "v16-beta-logs",
    }
    volumes_section = COMPOSE.split("\nvolumes:\n", 1)[1].split("\nsecrets:\n", 1)[0]
    declared = set(re.findall(r"^  ([a-z0-9-]+):$", volumes_section, re.MULTILINE))
    assert declared == expected
    for name in expected:
        assert COMPOSE.count(f"{name}:") >= 2


def test_retired_realtime_and_queue_services_are_absent():
    for service in ("websocket", "worker", "platform-websocket"):
        assert not re.search(rf"^  {service}:$", COMPOSE, re.MULTILINE)
    assert "profiles: [legacy]" not in COMPOSE


def test_scheduled_profile_pairs_the_scheduler_with_one_queue_consumer():
    scheduler = COMPOSE.split("  scheduler:\n",1)[1].split("\n  scheduler-worker:\n",1)[0]
    worker = COMPOSE.split("  scheduler-worker:\n",1)[1].split("\n  daily-provision:\n",1)[0]
    assert "profiles: [scheduled]" in scheduler
    assert 'command: ["bench", "schedule"]' in scheduler
    assert "mem_limit: 256m" in scheduler
    assert 'restart: "unless-stopped"' in scheduler
    assert "profiles: [scheduled]" in worker
    assert 'command: ["bench", "worker", "--queue", "short,default,long"]' in worker
    assert 'restart: "unless-stopped"' in worker
    assert "v16-sites:/home/frappe/frappe-bench/sites" in worker
    assert "v16-logs:/home/frappe/frappe-bench/logs" in worker
    assert "../frappe_app:/opt/dsherp-frappe:ro" in worker


def test_beta_backend_stays_internal_and_uses_the_separate_preview_entry():
    beta = COMPOSE.split("  beta-backend:\n", 1)[1].split("\n  platform-frontend:\n", 1)[0]
    assert "ports:" not in beta
    assert "networks: [validation]" in beta
    frontend = COMPOSE.split("  frontend:\n", 1)[1].split("\n  scheduler:\n", 1)[0]
    assert "127.0.0.1:18085:8081" in frontend


def test_platform_backend_has_enough_memory_for_v16_integration_reads():
    platform = COMPOSE.split("  platform-backend:\n", 1)[1].split("\n  beta-backend:\n", 1)[0]
    assert "mem_limit: 448m" in platform
    assert "memswap_limit: 448m" in platform


def test_agent_runtime_isolated_volume_is_v16_specific_everywhere():
    paths = [
        ROOT / "infra/prepare_agent_runtime.sh",
        ROOT / "dsherp/context_container.py",
        ROOT / "dsherp/context_worker.py",
    ]
    sources = [path.read_text() for path in paths]
    assert all("dsherp-v16-agent-runtime" in source for source in sources)
    assert all("dsherp-agent-runtime" not in source for source in sources)


def test_runtime_revision_covers_deployment_control_files():
    files = set(json.loads((ROOT / "config/runtime-files.json").read_text()))
    assert {
        "infra/compose.validation.yml",
        "infra/prepare_agent_runtime.sh",
        "dsherp/runtime_host.py",
        "dsherp/context_container.py",
        "dsherp/context_worker.py",
    } <= files


def test_context_worker_launch_agent_is_reproducible_and_self_restarting(tmp_path):
    from infra.render_context_worker_launch_agent import render_launch_agent

    target = render_launch_agent(ROOT, target=tmp_path / "worker.plist")
    launch_agent = plistlib.loads(target.read_bytes())
    assert launch_agent["Label"] == "com.dsherp.agent-worker-v16"
    assert launch_agent["WorkingDirectory"] == str(ROOT)
    assert launch_agent["ProgramArguments"] == [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "dsherp.context_worker",
        "--profile",
        str(ROOT / ".runtime/context-worker.json"),
        "--provider-env",
        str(ROOT / ".env"),
    ]
    assert launch_agent["RunAtLoad"] is True
    assert launch_agent["KeepAlive"] is True
    assert launch_agent["ThrottleInterval"] == 10
    assert launch_agent["EnvironmentVariables"]["PATH"].split(":") == [
        "/usr/local/bin","/opt/homebrew/bin","/usr/bin","/bin","/usr/sbin","/sbin",
    ]
    assert target.stat().st_mode & 0o777 == 0o600
