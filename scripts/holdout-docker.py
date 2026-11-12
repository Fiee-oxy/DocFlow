"""Docker Desktop CLI bridge for isolated evaluation; does not change app settings."""
import subprocess
import sys
import tempfile

CLI=['wsl','-d','docker-desktop','-e','/mnt/host/wsl/docker-desktop/cli-tools/usr/bin/docker',
     '--host','unix:///run/guest-services/docker.proxy.sock']
if __name__=='__main__':
    with tempfile.TemporaryFile() as errors:
        result=subprocess.run(CLI+sys.argv[1:],stderr=errors)
        if result.returncode:
            errors.seek(0);sys.stderr.buffer.write(errors.read())
    raise SystemExit(result.returncode)
