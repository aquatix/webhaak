#!/bin/bash
set -e
# Any subsequent(*) commands which fail will cause the shell script to exit immediately

if [ "$#" -ne 2 ]; then
    echo "USAGE: update_virtualenv.sh [virtualenv_path] [requirements_file]"
    exit 1
fi

if [ ! -f "$2" ]; then
    echo "requirements file '$2' not found"
    exit 2
fi

VIRTUALENVDIR="${1}"
REQUIREMENTSFILE="${2}"

if [ ! -d "${VIRTUALENVDIR}" ]; then
    echo "Creating virtualenv $1"
    mkdir -p "${VIRTUALENVDIR}"
    cd "${VIRTUALENVDIR}"
    virtualenv -p python3 .
    # python3 -m venv .
fi

if [[ -z ${VIRTUAL_ENV} ]]; then
    # Only activate the virtualenv if we aren't in one already
    source ${VIRTUALENVDIR}/bin/activate
    REQUIREMENTSDIR=$(dirname "${REQUIREMENTSFILE}")

    cd "$REQUIREMENTSDIR"

    # Make sure to run the latest pip and pip-tools
    pip install pip --upgrade
    pip install pip-tools --upgrade

    pip-sync "${REQUIREMENTSFILE}"
else
    echo "A virtualenv is already activated: $VIRTUAL_ENV"
    exit 3
fi
