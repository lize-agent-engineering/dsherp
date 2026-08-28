"""One scoped business runtime container; never mount the whole session store."""
from dsherp.agent_worker import IMAGE, container_base


def docker_command(root,secret,session_directory,name):
    return container_base(name)+[
        '-v',f'{root}/dsherp:/opt/dsherp/dsherp:ro',
        '-v',f'{root}/config:/opt/dsherp/config:ro',
        '-v',f'{root}/runtime:/opt/dsherp/runtime:ro',
        '-v',f'{root}/business-skills:/opt/dsherp/business-skills:ro',
        '-v',f'{root}/requirements.lock:/opt/dsherp/requirements.lock:ro',
        '-v','dsherp-agent-runtime:/opt/runtime:ro',
        '-v',f'{secret}:/run/business.json:ro',
        '-v',f'{session_directory}:/session:rw',
        '-e','PYTHONPATH=/opt/dsherp','-e','PYTHONDONTWRITEBYTECODE=1',
        '--workdir','/session','--entrypoint','/opt/runtime/bin/python',IMAGE,
        '-m','dsherp.context_runner']
