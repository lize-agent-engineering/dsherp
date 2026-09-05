"""One scoped business runtime container; never mount the whole session store.

Nothing about the control plane reaches this container: no compose definition, no
provisioning script and, in production, no working copy at all. The deployment
identity travels as one digest inside run.json instead of as mounted files.
"""
from dsherp import deploy_env
from dsherp.runtime_host import container_base


CODE = ('dsherp', 'config', 'runtime', 'business-skills')


def docker_command(root, secret, session_directory, name, resolved=None):
    resolved = resolved or deploy_env.settings()
    command = container_base(name, resolved)
    if resolved['env'] == 'dev':
        # Development runs the working copy; the release image carries the same tree.
        for directory in CODE:
            command += ['-v', f'{root}/{directory}:/opt/dsherp/{directory}:ro']
        command += ['-v', f'{root}/requirements.lock:/opt/dsherp/requirements.lock:ro',
                    '-v', 'dsherp-v16-agent-runtime:/opt/runtime:ro']
        image = resolved['base_image']
    else:
        image = resolved['worker_image']
    return command + [
        '-v', f'{secret}:/run/business.json:ro',
        '-v', f'{session_directory}:/session:rw',
        '-e', 'PYTHONPATH=/opt/dsherp', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '--workdir', '/session', '--entrypoint', '/opt/runtime/bin/python', image,
        '-m', 'dsherp.context_runner']
