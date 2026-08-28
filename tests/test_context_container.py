from pathlib import Path
from dsherp.context_container import docker_command


def test_business_container_mounts_only_current_session_and_run():
    command=docker_command(Path('/project'),Path('/work/run.json'),Path('/state/tenant/user/conversation'),'dsherp-context-test')
    mounts=[command[i+1] for i,v in enumerate(command) if v=='-v']
    assert '/work/run.json:/run/business.json:ro' in mounts
    assert '/state/tenant/user/conversation:/session:rw' in mounts
    assert '/project/runtime:/opt/dsherp/runtime:ro' in mounts
    assert '/project/business-skills:/opt/dsherp/business-skills:ro' in mounts
    assert not any('/state:' in m or '/project:/opt' in m for m in mounts)
    assert command[command.index('--memory')+1]=='384m'
    assert command[command.index('--cpus')+1]=='0.1'
    assert '--read-only' in command and '--cap-drop=ALL' in command
    assert command[-2:]==['-m','dsherp.context_runner']
