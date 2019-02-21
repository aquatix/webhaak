#!/bin/bash
set -e
# Any subsequent(*) commands which fail will cause the shell script to exit immediately

if [ "$#" -ne 2 ]; then
    echo "USAGE: update_virtualenv.sh [virtualenv_path] [requirements_path]"
    exit 1
fi

if [ ! -f "$2" ]; then
    echo "requirements file '$2' not found"
    exit 2
fi

if [ ! -d "$1" ]; then
    echo "Creating virtualenv $1"
    mkdir -p "$1"
    cd "$1"
    virtualenv -p python3 .
fi

if [[ -z ${VIRTUAL_ENV} ]]; then
    # Only activate the virtualenv if we aren't in one already
    #export VIRTUALENVWRAPPER_PYTHON=/usr/bin/python3
    #source /usr/share/virtualenvwrapper/virtualenvwrapper.sh
    #workon paragoo

    # No virtualenvwrapper for python 3 on Debian
    source ${1}/bin/activate
    REQUIREMENTSDIR=$(dirname "${VAR}")

    cd "$REQUIREMENTSDIR"

    pip install pip-tools --upgrade
    pip-sync ${2}
else
    echo "A virtualenv is already activated: $VIRTUAL_ENV"
    exit 3
fi
