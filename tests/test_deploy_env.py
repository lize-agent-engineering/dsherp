"""One environment file decides images, domains, networks; nothing is read from the working copy."""
import os

import pytest

from dsherp import deploy_env


DEV = {
    "DSHERP_ENV": "dev",
    "DSHERP_PROJECT": "dsherp-validation",
    "DSHERP_BASE_DOMAIN": "localhost",
    "DSHERP_PLATFORM_SLUG": "dsherp-platform",
    "DSHERP_AGENT_UID": "501",
    "DSHERP_AGENT_GID": "20",
    "DSHERP_ORIGINS": "dsherp-validation=http://localhost:18082,dsherp-platform=http://platform.localhost:18083",
}
PROD = {
    "DSHERP_ENV": "prod",
    "DSHERP_PROJECT": "dsherp",
    "DSHERP_BASE_DOMAIN": "tenant.example.com",
    "DSHERP_PLATFORM_SLUG": "platform",
    "DSHERP_IMAGE_TAG": "v0.3.0",
    "DSHERP_IMAGE_REGISTRY": "registry.example.com/dsherp",
    "DSHERP_AGENT_UID": "1000",
    "DSHERP_AGENT_GID": "1000",
}


def test_dev_keeps_the_pinned_upstream_image_and_the_current_project_name():
    values = deploy_env.settings(DEV)
    assert values["env"] == "dev"
    assert values["frappe_image"] == deploy_env.BASE_IMAGE
    assert values["worker_image"] == deploy_env.BASE_IMAGE
    assert values["project"] == "dsherp-validation"
    assert values["agent_network"] == "dsherp-validation_agent"
    assert values["scheme"] == "http"


def test_production_images_come_from_the_release_tag_and_never_from_a_working_copy():
    values = deploy_env.settings(PROD)
    assert values["frappe_image"] == "registry.example.com/dsherp/dsherp-frappe:v0.3.0"
    assert values["worker_image"] == "registry.example.com/dsherp/dsherp-worker:v0.3.0"
    assert values["agent_network"] == "dsherp_agent"
    assert values["scheme"] == "https"
    assert values["image_tag"] == "v0.3.0"


def test_production_without_a_release_tag_fails_instead_of_defaulting_to_latest():
    with pytest.raises(ValueError):
        deploy_env.settings({**PROD, "DSHERP_IMAGE_TAG": ""})
    with pytest.raises(ValueError):
        deploy_env.settings({**PROD, "DSHERP_IMAGE_TAG": "latest"})


def test_unknown_environment_and_bad_domain_or_project_fast_fail():
    with pytest.raises(ValueError):
        deploy_env.settings({**DEV, "DSHERP_ENV": "staging"})
    with pytest.raises(ValueError):
        deploy_env.settings({**DEV, "DSHERP_BASE_DOMAIN": "Local Host"})
    with pytest.raises(ValueError):
        deploy_env.settings({**DEV, "DSHERP_PROJECT": "Dsherp Validation"})


def test_the_agent_container_never_runs_as_root():
    with pytest.raises(ValueError):
        deploy_env.settings({**PROD, "DSHERP_AGENT_UID": "0"})
    with pytest.raises(ValueError):
        deploy_env.settings({**PROD, "DSHERP_AGENT_UID": "-1"})
    assert deploy_env.settings(PROD)["agent_user"] == "1000:1000"


def test_agent_uid_defaults_to_the_calling_host_user(tmp_path):
    # root=tmp_path: a local, gitignored infra/env/prod.env must not reach a unit test.
    values = deploy_env.settings({key: value for key, value in PROD.items() if not key.startswith("DSHERP_AGENT_")},
                                 root=tmp_path)
    assert values["agent_user"] == f"{os.getuid()}:{os.getgid()}"


def test_site_names_and_origins_are_derived_from_the_base_domain():
    prod = deploy_env.settings(PROD)
    assert deploy_env.site_name(prod, "acme") == "acme.tenant.example.com"
    assert deploy_env.public_origin(prod, "acme") == "https://acme.tenant.example.com"
    assert deploy_env.callback_url(prod, "acme") == "https://acme.tenant.example.com/api/method/dsherp_bridge.sso.callback"
    assert deploy_env.start_url(prod, "acme") == "https://acme.tenant.example.com/api/method/dsherp_bridge.sso.start"
    assert prod["platform_site"] == "platform.tenant.example.com"


def test_dev_origins_override_the_derived_ones_so_existing_ports_keep_working():
    dev = deploy_env.settings(DEV)
    assert deploy_env.public_origin(dev, "dsherp-validation") == "http://localhost:18082"
    assert deploy_env.public_origin(dev, "dsherp-platform") == "http://platform.localhost:18083"
    assert deploy_env.public_origin(dev, "other") == "http://other.localhost"


def test_slugs_are_validated_before_they_reach_a_site_name_or_a_url():
    prod = deploy_env.settings(PROD)
    for bad in ("", "Acme", "acme.evil", "-acme", "acme/../etc"):
        with pytest.raises(ValueError):
            deploy_env.site_name(prod, bad)


def test_the_environment_file_supplies_values_and_the_process_environment_wins(tmp_path):
    directory = tmp_path / "infra" / "env"
    directory.mkdir(parents=True)
    (directory / "prod.env").write_text(
        "DSHERP_ENV=prod\nDSHERP_PROJECT=dsherp\nDSHERP_BASE_DOMAIN=file.example.com\n"
        "DSHERP_PLATFORM_SLUG=platform\nDSHERP_IMAGE_TAG=v0.1.0\nDSHERP_AGENT_UID=1000\nDSHERP_AGENT_GID=1000\n"
    )
    values = deploy_env.settings({"DSHERP_ENV": "prod"}, root=tmp_path)
    assert values["base_domain"] == "file.example.com" and values["image_tag"] == "v0.1.0"
    overridden = deploy_env.settings({"DSHERP_ENV": "prod", "DSHERP_IMAGE_TAG": "v0.2.0"}, root=tmp_path)
    assert overridden["image_tag"] == "v0.2.0"


def test_missing_environment_file_for_production_fails_loudly(tmp_path):
    with pytest.raises(ValueError):
        deploy_env.settings({"DSHERP_ENV": "prod"}, root=tmp_path)


def test_the_host_directories_come_from_the_environment_file_and_are_absolute_in_production(tmp_path):
    values = deploy_env.settings({**PROD, "DSHERP_RUNTIME_DIR": "/srv/dsherp/state",
                                  "DSHERP_SECRETS_DIR": "/srv/dsherp/state/control"})
    assert str(values["runtime_dir"]) == "/srv/dsherp/state"
    assert str(values["secrets_dir"]) == "/srv/dsherp/state/control"
    defaults = deploy_env.settings(DEV, root=tmp_path)
    assert defaults["runtime_dir"] == tmp_path / ".runtime"
    assert defaults["secrets_dir"] == tmp_path / ".runtime" / "control"
    with pytest.raises(ValueError):
        deploy_env.settings({**PROD, "DSHERP_RUNTIME_DIR": "relative/state"})
