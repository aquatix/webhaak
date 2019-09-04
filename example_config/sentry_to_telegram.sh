#!/bin/bash

# Any subsequent(*) commands which fail will cause the shell script to exit immediately
set -e

if [ "$#" -ne 5 ]; then
    echo "USAGE: sentry_to_telegram.sh [projectname] [culprit] [url] [message] [stacktrace]"
    exit 1
fi

PROJECTNAME="${1}"
CULPRIT="${2}"
URL="${3}"
MESSAGE="${4}"
STACKTRACE="${5}"

# Filter away known things
if [[ $MESSAGE == *"Het ElementTree object kon niet"* ||
      $MESSAGE == *"Meerdere resultaten gevonden in "* ||
      $MESSAGE == *"Found multiple results in"* ||
      $MESSAGE == *"Cannot find object for id"* ]];
then
    exit
fi

# Make the URL a bit more neat
URL=${URL//?referrer=webhooks_plugin/}

if [ $STACKTRACE != "Not available" ]; then
    TRACETEXT="
${STACKTRACE}

"

# Replace literal \n with end of lines
TRACETEXT=${TRACETEXT//\\n/
}
fi

# The message to send
REPORT="[${PROJECTNAME}] ${MESSAGE}

in *${CULPRIT}*
${TRACETEXT}
${URL}"

# AwesomeCorp dev groupchat
CHATID="-4242424242"
KEY="YOUR:KEY-HERE"

curl -s -G \
     --data-urlencode "text=${REPORT}" \
     --data-urlencode "chat_id=${CHATID}" \
     --data-urlencode "disable_web_page_preview=true" \
     --data-urlencode "parse_mode=Markdown" \
    "https://api.telegram.org/bot${KEY}/sendMessage" > /dev/null
