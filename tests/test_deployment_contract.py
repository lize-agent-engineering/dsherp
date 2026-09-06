"""Static deployment guards: what a production host will actually run.

These assertions are the cheap half of gates G1 and G4. They cannot prove a clean
Linux host comes up, but they do fail the moment an entry point drifts back to an
unpinned image, a root container, a routable agent network or a mounted working copy.
"""
import json
import plistlib
import re
from pathlib import Path

import pytest

from dsherp import deploy_env


ROOT = Path(__file__).parents[1]
DEV_COMPOSE_PATH = ROOT / "infra/compose.validation.yml"
PROD_COMPOSE_PATH = ROOT / "infra/compose.prod.yml"
DEV_COMPOSE = DEV_COMPOSE_PATH.read_text()
PROD_COMPOSE = PROD_COMPOSE_PATH.read_text()
ERP_V15 = "cf5905396635aa2ee91722237e489bf0ab848819c521d094703852f154cdb341"
ERP_V16 = "493cecf82c92c828bf0d0c57df60694e07dc61671e374ac93a070d1cc86df1bd"
DB_V15 = "92e50059ea0a5965a33ef751970eab37d421b91ebbd01ac909039cffe159e574"
DB_V16 = "2439dcd7d14010ecd1ff7a4e1c5abe8e208c34fe35290744deeeaac3569043c3"
# Every file that can name a base image. A new one must be added here on purpose.
IMAGE_SOURCES = (
    "infra/compose.validation.yml",
    "infra/prepare_agent_runtime.sh",
    "infra/docker/frappe/Dockerfile",
    "infra/docker/worker/Dockerfile",
    "infra/probe_v16/compose.yml",
    "dsherp/deploy_env.py",
    "dsherp/runtime_host.py",
)


def _block(text, name, indent=2):
    """Return one mapping entry's body: every line indented deeper than its key."""
    prefix = " " * indent + name + ":"
    lines = text.splitlines()
    for position, line in enumerate(lines):
        if line.startswith(prefix) and line.rstrip() in (prefix, prefix + " {}"):
            body = []
            for following in lines[position + 1:]:
                if following.strip() and not following.startswith(" " * (indent + 1)):
                    break
                body.append(following)
            return "\n".join(body)
        if line.startswith(prefix):
            return line[len(prefix):]
    raise AssertionError("no such block: " + name)


def test_no_deployment_entrypoint_can_reach_a_retired_v15_image():
    for name in IMAGE_SOURCES:
        source = (ROOT / name).read_text()
        assert ERP_V15 not in source, name
        assert DB_V15 not in source, name


def test_every_base_image_reference_is_a_digest_and_the_pinned_one():
    for name in IMAGE_SOURCES:
        source = (ROOT / name).read_text()
        for reference in re.findall(r"frappe/erpnext[@:][^\s\"']+", source):
            assert reference == f"frappe/erpnext@sha256:{ERP_V16}", (name, reference)
        for reference in re.findall(r"\bmariadb[@:][^\s\"']+", source):
            assert reference == f"mariadb@sha256:{DB_V16}", (name, reference)
    assert deploy_env.BASE_IMAGE == f"frappe/erpnext@sha256:{ERP_V16}"


def test_production_pulls_every_third_party_image_by_digest_and_ours_by_release_tag():
    references = re.findall(r"^\s+image: (.+)$", PROD_COMPOSE, re.MULTILINE)
    assert references, "production compose declares no image"
    for reference in references:
        assert "@sha256:" in reference or reference.startswith("${DSHERP_IMAGE_REGISTRY"), reference
    assert "${DSHERP_IMAGE_TAG:?set DSHERP_IMAGE_TAG}" in PROD_COMPOSE
    assert "pull_policy: never" not in PROD_COMPOSE


def test_production_mounts_no_working_copy_and_no_host_path():
    volumes = re.findall(r"^\s+- ([^\s#]+:[^\s#]+)$", PROD_COMPOSE, re.MULTILINE)
    assert volumes, "production compose declares no volume"
    for volume in volumes:
        source = volume.split(":", 1)[0]
        assert not source.startswith((".", "/", "$")), volume
        assert re.fullmatch("[a-z0-9-]+", source), volume


def test_production_publishes_only_the_ingress_and_a_loopback_port_for_the_host_worker():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    published = {}
    for service in re.findall(r"^  ([a-z0-9-]+):$", body, re.MULTILINE):
        block = _block(body, service)
        if "ports:" in block:
            published[service] = re.findall(r'"([^"]+)"', _block(block, "ports", indent=4) or block.split("ports:", 1)[1].split("\n", 1)[0])
    assert set(published) == {"caddy", "backend"}, published
    assert all(entry.startswith("127.0.0.1:") for entry in published["backend"]), published["backend"]
    # Host side defaults to 80/443 and may be moved; the container side never moves.
    # rsplit: the host side is a ${VAR:-default} expression and itself contains a colon.
    assert {entry.rsplit(":", 1)[0] for entry in published["caddy"]} == {"${DSHERP_HTTP_PORT:-80}", "${DSHERP_HTTPS_PORT:-443}"}
    assert {entry.rsplit(":", 1)[1] for entry in published["caddy"]} == {"80", "443", "443/udp"}
    # Docker publishes nothing for a container that is only on internal networks.
    networks = PROD_COMPOSE.split("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "internal" not in _block(networks, "worker")
    assert "worker" in _block(_block(body, "backend"), "networks", indent=4)
    assert not [service for service in re.findall(r"^  ([a-z0-9-]+):$", body, re.MULTILINE)
                if service != "backend" and "worker" in (_block(_block(body, service), "networks", indent=4) or "")]


def test_production_front_ends_serve_a_read_only_sites_volume_without_the_image_entrypoint():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    for service, volume in (("frontend", "tenant-sites"), ("platform-frontend", "platform-sites")):
        block = _block(body, service)
        assert f"{volume}:/home/frappe/frappe-bench/sites:ro" in block, service
        # The image entrypoint would rm -rf sites/assets on start and die on :ro.
        assert "entrypoint: []" in block and 'command: ["nginx-entrypoint.sh"]' in block, service


def test_production_keeps_every_long_lived_service_supervised_and_probed():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    services = re.findall(r"^  ([a-z0-9-]+):$", body, re.MULTILINE)
    assert {"db", "redis-cache", "redis-queue", "backend", "frontend",
            "platform-backend", "platform-frontend", "agent-egress", "caddy"} <= set(services)
    assert body.count("restart: unless-stopped") + body.count("<<: *frappe") >= len(services)
    # Every service, including the four bench supervisors the spec topology names explicitly.
    for service in services:
        assert "healthcheck:" in _block(body, service), service
    assert len(services) == 13


def test_the_agent_network_has_no_route_out_in_either_environment():
    for compose, name in ((DEV_COMPOSE, "dev"), (PROD_COMPOSE, "prod")):
        section = compose.split("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]
        assert "internal: true" in _block(section, "agent"), name


def test_the_control_plane_front_end_is_not_reachable_from_the_agent_network():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    platform = _block(body, "platform-backend")
    assert "agent" not in _block(platform, "networks", indent=4)
    dev_body = DEV_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    assert "agent" not in _block(_block(dev_body, "platform-frontend"), "networks", indent=4)
    assert "agent" in _block(_block(body, "backend"), "networks", indent=4)


def test_the_only_way_out_of_the_agent_network_is_the_provider_proxy():
    egress = (ROOT / "infra/nginx/agent-egress.conf").read_text()
    assert "proxy_pass https://$provider$request_uri;" in egress
    assert "proxy_ssl_verify on;" in egress
    assert "DSHERP_PROVIDER_HOST" in egress
    for compose in (DEV_COMPOSE, PROD_COMPOSE):
        assert "agent-egress" in compose


def test_the_browser_gets_a_content_security_policy_from_a_versioned_file():
    headers = (ROOT / "infra/nginx/security-headers.conf").read_text()
    assert "Content-Security-Policy" in headers
    assert "img-src 'self' data: blob:" in headers
    assert "connect-src 'self'" in headers
    assert "object-src 'none'" in headers
    assert "/etc/nginx/snippets/security_headers.conf" in DEV_COMPOSE
    dockerfile = (ROOT / "infra/docker/frappe/Dockerfile").read_text()
    assert "COPY infra/nginx/security-headers.conf /etc/nginx/snippets/security_headers.conf" in dockerfile


def test_the_benches_can_stage_backup_sets_in_a_data_volume_and_a_separate_secrets_volume():
    """A backup set's data half and secret half never share a volume, so a sync container for
    one half cannot even see the other."""
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    for service, prefix in (("backend", "tenant"), ("platform-backend", "platform")):
        block = _block(body, service)
        assert f"{prefix}-backups:/home/frappe/backups" in block, service
        assert f"{prefix}-backup-secrets:/home/frappe/backup-secrets" in block, service
    volumes = PROD_COMPOSE.split("\nvolumes:\n", 1)[1].split("\n\n", 1)[0]
    for name in ("tenant-backup-secrets", "platform-backup-secrets"):
        assert f"  {name}:" in volumes, name
    # The two benches never share either kind of staging volume.
    assert "tenant-backup-secrets" not in _block(body, "platform-backend")
    assert "platform-backups" not in _block(body, "backend")
    dockerfile = (ROOT / "infra/docker/frappe/Dockerfile").read_text()
    assert "/home/frappe/backups" in dockerfile and "/home/frappe/backup-secrets" in dockerfile
    for name in ("v16-backups", "v16-backup-secrets", "v16-platform-backups", "v16-platform-backup-secrets"):
        assert f"  {name}:" in DEV_COMPOSE, name


def test_production_keeps_the_two_benches_apart_and_caddy_without_capabilities():
    body = PROD_COMPOSE.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    for service in ("scheduler", "queue"):
        assert "tenant-backups:/home/frappe/backups" in _block(body, service), service
    for service in ("platform-scheduler", "platform-queue"):
        assert "platform-backups:/home/frappe/backups" in _block(body, service), service
    assert "backups:/home/frappe/backups" not in PROD_COMPOSE.replace("tenant-backups:", "").replace("platform-backups:", "")
    caddy = _block(body, "caddy")
    assert "cap_drop: [ALL]" in caddy and "cap_add: [NET_BIND_SERVICE]" in caddy
    networks = PROD_COMPOSE.split("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "DSHERP_EDGE_SUBNET" in _block(networks, "edge")
    assert "${DSHERP_EDGE_SUBNET:-10.90.0.0/24}" in _block(_block(body, "frontend"), "environment", indent=4)


def test_a_retired_site_lands_on_a_volume_the_image_prepared_for_the_bench_user():
    """`bench drop-site` moves the whole Site directory under the bench's archived/; the
    backend only persisted sites/ and logs/, so a retired tenant lived in the container's
    writable layer until the next recreate. The directory must exist in the image owned by
    the bench user, or the empty named volume is root-owned and the move fails after the
    database is already gone."""
    backend = _block(PROD_COMPOSE, "backend")
    assert "tenant-archive:/home/frappe/frappe-bench/archived" in backend
    assert "tenant-archive:" in PROD_COMPOSE.split("\nvolumes:\n", 1)[1]
    # release archives each bench's pre-upgrade backup set on the same volume, the platform's too.
    assert "platform-archive:/home/frappe/frappe-bench/archived" in _block(PROD_COMPOSE, "platform-backend")
    assert "platform-archive:" in PROD_COMPOSE.split("\nvolumes:\n", 1)[1]
    dockerfile = (ROOT / "infra/docker/frappe/Dockerfile").read_text()
    assert re.search(r"install -d .*-o frappe -g frappe .*/home/frappe/frappe-bench/archived", dockerfile), dockerfile
    from dsherp import admin
    assert admin.ARCHIVE == "/home/frappe/frappe-bench/archived/sites"


def test_the_build_context_excludes_runtime_state_secrets_and_tooling():
    """The release images are built from the working directory; without a .dockerignore the
    daemon receives .runtime (credentials), infra/env (filled environments) and the venv."""
    ignored = (ROOT / ".dockerignore").read_text().split()
    for entry in (".git", ".runtime", ".venv", "work", "infra/env", "frontend/node_modules", "evals/runs", "*.log"):
        assert entry in ignored, entry
    # What the Dockerfiles COPY must not be shadowed by an ignore pattern.
    for kept in ("frappe_app", "infra/nginx", "requirements.lock", "dsherp", "config", "runtime", "business-skills"):
        assert not any(pattern in (kept, kept + "/", "**/" + kept) for pattern in ignored), kept
    assert not any(pattern in ("dist", "dist/", "**/dist") for pattern in ignored)


def test_the_public_edge_does_not_expose_the_run_capability_endpoints():
    # Development renders infra/frappe.conf.template; the release image ships its own
    # template over the base image's, so both must carry the same block.
    for name in ("infra/frappe.conf.template", "infra/nginx/site.conf.template"):
        template = (ROOT / name).read_text()
        block = template.split("dsherp_bridge\\.context_execution", 1)[1].split("}", 1)[0]
        for endpoint in ("run_status", "reserve_model_call", "run_tool", "record_run_event", "finish_run"):
            assert endpoint in block, name
        assert "return 404;" in block, name
        # Audit finding: Frappe also serves /api/v2/method/... and ?cmd=...; both must 404 too.
        assert "^/api/(v[0-9]+/)?method/" in template, name
        assert "$arg_cmd" in template and template.count("return 404;") >= 2, name
    dockerfile = (ROOT / "infra/docker/frappe/Dockerfile").read_text()
    assert "COPY infra/nginx/site.conf.template /templates/nginx/frappe.conf.template" in dockerfile


def test_compose_uses_only_fresh_v16_named_volumes():
    expected = {
        "v16-sites", "v16-logs", "v16-db-data", "v16-redis-data",
        "v16-platform-sites", "v16-platform-logs", "v16-beta-sites", "v16-beta-logs",
        # Backup sets are staged here in development too, so a drill exercises the real layout.
        "v16-backups", "v16-backup-secrets", "v16-platform-backups", "v16-platform-backup-secrets",
    }
    volumes_section = DEV_COMPOSE.split("\nvolumes:\n", 1)[1].split("\nsecrets:\n", 1)[0]
    declared = set(re.findall(r"^  ([a-z0-9-]+):$", volumes_section, re.MULTILINE))
    assert declared == expected
    for name in expected:
        assert DEV_COMPOSE.count(f"{name}:") >= 2


def test_retired_realtime_and_queue_services_are_absent():
    for service in ("websocket", "worker", "platform-websocket"):
        assert not re.search(rf"^  {service}:$", DEV_COMPOSE, re.MULTILINE)
    assert "profiles: [legacy]" not in DEV_COMPOSE


def test_scheduled_profile_pairs_the_scheduler_with_one_queue_consumer():
    scheduler = DEV_COMPOSE.split("  scheduler:\n", 1)[1].split("\n  scheduler-worker:\n", 1)[0]
    worker = DEV_COMPOSE.split("  scheduler-worker:\n", 1)[1].split("\n  daily-provision:\n", 1)[0]
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
    beta = DEV_COMPOSE.split("  beta-backend:\n", 1)[1].split("\n  platform-frontend:\n", 1)[0]
    assert "ports:" not in beta
    assert "networks: [validation]" in beta
    frontend = DEV_COMPOSE.split("  frontend:\n", 1)[1].split("\n  scheduler:\n", 1)[0]
    assert "127.0.0.1:18085:8081" in frontend


def test_platform_backend_has_enough_memory_for_v16_integration_reads():
    platform = DEV_COMPOSE.split("  platform-backend:\n", 1)[1].split("\n  beta-backend:\n", 1)[0]
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


def test_the_runtime_manifest_lists_only_what_the_run_container_can_see():
    files = json.loads((ROOT / "config/runtime-files.json").read_text())
    assert "infra/compose.validation.yml" not in files
    assert "infra/prepare_agent_runtime.sh" not in files
    assert not [name for name in files if name.startswith("infra/")]
    assert {"dsherp/context_runner.py", "dsherp/context_mcp.py", "config/dsh-context.yml",
            "runtime/model-guard.cjs", "requirements.lock"} <= set(files)


def test_the_deployment_digest_binds_the_files_a_container_no_longer_sees(tmp_path):
    resolved = deploy_env.settings({"DSHERP_ENV": "dev"})
    assert "infra/compose.validation.yml" in deploy_env.DEPLOYMENT_FILES
    assert "infra/prepare_agent_runtime.sh" in deploy_env.DEPLOYMENT_FILES
    assert "infra/compose.prod.yml" in deploy_env.DEPLOYMENT_FILES
    for name in deploy_env.DEPLOYMENT_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    first = deploy_env.deployment_digest(resolved, tmp_path)
    assert re.fullmatch("[a-f0-9]{64}", first)
    (tmp_path / deploy_env.DEPLOYMENT_FILES[0]).write_text("changed")
    assert deploy_env.deployment_digest(resolved, tmp_path) != first
    production = deploy_env.settings({
        "DSHERP_ENV": "prod", "DSHERP_PROJECT": "dsherp", "DSHERP_BASE_DOMAIN": "tenant.example.com",
        "DSHERP_PLATFORM_SLUG": "platform", "DSHERP_IMAGE_TAG": "v0.3.0",
        "DSHERP_AGENT_UID": "1000", "DSHERP_AGENT_GID": "1000"})
    assert deploy_env.deployment_digest(production, ROOT) != deploy_env.deployment_digest(resolved, ROOT)


def test_the_production_worker_unit_restarts_itself_and_owns_only_two_directories(tmp_path):
    from infra.render_worker_units import render_systemd_unit

    target = render_systemd_unit(ROOT, user="dsherp", group="dsherp", target=tmp_path / "worker.service")
    unit = target.read_text()
    assert "Type=notify" in unit and "NotifyAccess=main" in unit
    assert "Restart=always" in unit and "RestartSec=10" in unit
    assert re.search(r"^WatchdogSec=\d+s$", unit, re.MULTILINE)
    assert "ProtectSystem=strict" in unit and "NoNewPrivileges=true" in unit
    # work/ only appears once a run starts; a systemd that honours ReadWritePaths must not
    # fail namespace setup on its absence.
    assert f"ReadWritePaths={ROOT}/.runtime -{ROOT}/work" in unit
    assert "SupplementaryGroups=docker" in unit
    assert f"ExecStart={ROOT}/.venv/bin/python -m dsherp.context_worker" in unit
    assert "TimeoutStopSec=120" in unit
    assert "User=dsherp" in unit
    # Found on the x86_64 drill: an unset DSHERP_ENV made the worker check for the dev volume.
    assert "Environment=DSHERP_ENV=prod" in unit


def test_the_firewall_unit_applies_host_rules_before_the_worker_and_the_worker_requires_it(tmp_path):
    """The INPUT rules that keep run containers off the host did not survive a reboot or a
    recreated agent network: only a printed suggestion existed. A oneshot unit re-derives the
    bridge and applies them; the worker cannot start without it."""
    from infra.render_worker_units import FIREWALL_UNIT_NAME, render_firewall_unit, render_systemd_unit

    assert FIREWALL_UNIT_NAME == "dsherp-agent-firewall.service"
    unit = render_firewall_unit(ROOT, agent_network="dsherp_agent", target=tmp_path / "fw.service").read_text()
    assert "Type=oneshot" in unit and "RemainAfterExit=yes" in unit
    assert "ExecStart=/usr/local/sbin/dsherp-agent-firewall apply dsherp_agent" in unit
    assert "ExecStop=/usr/local/sbin/dsherp-agent-firewall remove dsherp_agent" in unit
    assert re.search(r"^Requires=docker.service$", unit, re.MULTILINE)
    assert re.search(r"^After=docker.service$", unit, re.MULTILINE)
    assert "WantedBy=multi-user.target" in unit
    worker = render_systemd_unit(ROOT, user="dsherp", group="dsherp", target=tmp_path / "worker.service").read_text()
    assert re.search(r"^Requires=docker.service dsherp-agent-firewall.service$", worker, re.MULTILINE)
    assert re.search(r"^After=docker.service network-online.target dsherp-agent-firewall.service$", worker, re.MULTILINE)
    for bad in ("bad name", "", "dsherp agent;rm"):
        with pytest.raises(ValueError):
            render_firewall_unit(ROOT, agent_network=bad, target=tmp_path / "bad.service")


def test_the_firewall_script_tags_its_rules_records_the_bridge_and_is_valid_shell():
    import subprocess

    script = ROOT / "infra/systemd/dsherp-agent-firewall.sh"
    text = script.read_text()
    assert text.startswith("#!/bin/sh")
    assert script.stat().st_mode & 0o111, "must be executable"
    for needle in ("apply)", "check)", "remove)", "docker network inspect", "-m comment --comment",
                   "ESTABLISHED,RELATED", "-j DROP", "/run/dsherp-agent-firewall"):
        assert needle in text, needle
    assert subprocess.run(["sh", "-n", str(script)], capture_output=True).returncode == 0


def test_the_worker_unit_refuses_an_unusable_account_or_watchdog(tmp_path):
    from infra.render_worker_units import render_systemd_unit

    for bad in ({"user": "root user"}, {"user": ""}, {"user": "dsherp", "watchdog": 5},
                {"user": "dsherp", "stop_timeout": 5}):
        with pytest.raises(ValueError):
            render_systemd_unit(ROOT, target=tmp_path / "bad.service", **{"group": "dsherp", **bad})


def test_the_worker_answers_the_watchdog_and_reports_readiness():
    source = (ROOT / "dsherp/context_worker.py").read_text()
    assert "sd_notify.ready()" in source
    assert "sd_notify.watchdog()" in source


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
        str(ROOT / ".runtime/context-worker-sites.json"),
        "--provider-env",
        str(ROOT / ".env"),
    ]
    assert launch_agent["RunAtLoad"] is True
    assert launch_agent["KeepAlive"] is True
    assert launch_agent["ThrottleInterval"] == 10
    assert target.stat().st_mode & 0o777 == 0o600


def test_merge_context_worker_profiles_writes_merged_sites_without_changing_inputs(tmp_path):
    from infra.merge_context_worker_profiles import merge_context_worker_profiles

    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    alpha = {
        "site": "dsherp-validation.localhost",
        "base_url": "http://127.0.0.1:18081",
        "business_url": "http://dsherp-validation-backend-1:8000",
        "api_key": "alpha-key",
        "api_secret": "alpha-secret",
    }
    daily = {
        "site": "dsherp-daily.localhost",
        "base_url": "http://127.0.0.1:18082",
        "business_url": "http://dsherp-validation-backend-1:8000",
        "api_key": "daily-key",
        "api_secret": "daily-secret",
    }
    alpha_path = runtime / "context-worker.json"
    daily_path = runtime / "context-worker-daily.json"
    alpha_bytes = json.dumps(alpha, separators=(",", ":")).encode()
    daily_bytes = json.dumps(daily, separators=(",", ":")).encode()
    alpha_path.write_bytes(alpha_bytes)
    daily_path.write_bytes(daily_bytes)

    target = merge_context_worker_profiles(tmp_path)
    assert target == runtime / "context-worker-sites.json"
    assert target.stat().st_mode & 0o777 == 0o600
    merged = json.loads(target.read_text())
    assert merged["slots"] == 3
    assert merged["sites"] == [alpha, daily]
    assert alpha_path.read_bytes() == alpha_bytes
    assert daily_path.read_bytes() == daily_bytes


def test_merge_context_worker_profiles_fast_fails_on_missing_or_invalid_input(tmp_path):
    from infra.merge_context_worker_profiles import merge_context_worker_profiles

    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    alpha = runtime / "context-worker.json"
    daily = runtime / "context-worker-daily.json"

    with pytest.raises(ValueError):
        merge_context_worker_profiles(tmp_path)

    alpha.write_text(json.dumps({"site": "alpha.localhost"}))
    daily.write_text("[]")
    with pytest.raises(ValueError):
        merge_context_worker_profiles(tmp_path)

    daily.write_text(json.dumps({"site": ""}))
    with pytest.raises(ValueError):
        merge_context_worker_profiles(tmp_path)

    daily.write_text(json.dumps({"site": "alpha.localhost"}))
    with pytest.raises(ValueError):
        merge_context_worker_profiles(tmp_path)
