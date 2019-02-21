#!/bin/bash
set -e
# Any subsequent(*) commands which fail will cause the shell script to exit immediately

if [ "$#" -ne 2 ]; then
    echo "USAGE: update_flask.sh [projectname] [repodir]"
    exit 1
fi

PROJECTNAME="${1}"
REPODIR="${2}"
# Convention of having the virtualenv under the same parent dir as the project
VENVDIR="${2}/../venv/"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Assume update_virtualenv.sh lives in the same directory as this one
$DIR/update_virtualenv.sh "${VENVDIR}" "${REPODIR}/requirements.txt"

# Restart the project
sudo /usr/bin/supervisorctl restart "${PROJECTNAME}"
