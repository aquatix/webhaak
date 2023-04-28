#!/bin/bash

MAIL_FROM="CHANGEME@example.com"

VENVDIR="${1}"
PROJECTNAME="${2}"
REPODIR="${3}"
EMAIL="${4}"
COMMIT="${5}"

source "${VENVDIR}"

cd "${REPODIR}"

echo
echo "== flake8 ======"
echo "Checking changes for ${PROJECTNAME}"
echo "Changes by ${EMAIL}"
echo "${COMMIT}"
echo

FLAKE8RESULT=$(git -c advice.detachedHead=false checkout "${COMMIT}" && flake8 --config tox.ini)

echo "${FLAKE8RESULT}"

ISORTRESULT=$(isort -c . | grep -v Skipped)

if [ ! -z "${FLAKE8RESULT}${ISORTRESULT}" ]; then
    echo "Sending email"
    TEXT="Changes by ${EMAIL}\nDetails: ${COMMIT}\n\nflake8:\n\n${FLAKE8RESULT}\n\nisort:\n\n${ISORTRESULT}"
    echo -e "${TEXT}" | /usr/bin/mail -s "flake8 and isort results for ${PROJECTNAME}" -a "From: ${MAIL_FROM}" ${EMAIL}
else
    echo "Nothing to mail"
fi
echo "== Done with flake8 ======"
