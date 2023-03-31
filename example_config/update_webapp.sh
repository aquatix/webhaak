#!/bin/bash
set -e
# Any subsequent(*) commands which fail will cause the shell script to exit immediately

if [ "$#" -ne 3 ]; then
    echo "USAGE: update_webapp.sh [projectname] [repodir] [venvdir]"
    exit 1
fi

PROJECTNAME="${1}"
REPODIR="${2}"
VENVDIR="${3}"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Assume update_virtualenv.sh lives in the same directory as this one
$DIR/update_virtualenv.sh "${VENVDIR}" "${REPODIR}/requirements-server.txt"

# Restart the project
sudo /usr/bin/systemctl restart "${PROJECTNAME}.service"
sudo /usr/bin/supervisorctl restart "${PROJECTNAME}_rq_worker"
