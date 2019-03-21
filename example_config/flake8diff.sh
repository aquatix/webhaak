#!/bin/bash

VENVDIR="${1}"
PROJECTNAME="${2}"
REPODIR="${3}"
EMAIL="${4}"
COMMIT_BEFORE="${5}"
COMMIT_AFTER="${6}"
COMPARE_URL="${7}"

# Activate venv with flake8
source "${VENVDIR}"

# Go to the project to test
cd "${REPODIR}"

echo
echo "== flake8 ======"
echo "Checking changes for ${PROJECTNAME}"
echo "Changes by ${EMAIL}"
echo "${COMMIT_BEFORE}"
echo "${COMMIT_AFTER}"
echo "${COMPARE_URL}"
echo

RESULT=$(git diff -u "${COMMIT_BEFORE}" "${COMMIT_AFTER}" | flake8 --diff)

echo "${RESULT}"

if [ ! -z "${RESULT}" ]; then
    echo "Sending email"
    TEXT="Changes by ${EMAIL}\nDetails: ${COMPARE_URL}\n\nResults:\n\n${RESULT}"
    echo -e "${TEXT}" | /usr/bin/mail -s "flake8 results for ${PROJECTNAME}" ${EMAIL}
else
    echo "Nothing to mail"
fi
echo "== Done with flake8 ======"
