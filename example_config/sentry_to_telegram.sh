#!/bin/bash
set -e
# Any subsequent(*) commands which fail will cause the shell script to exit immediately

if [ "$#" -ne 4 ]; then
    echo "USAGE: sentry_to_telegram.sh [projectname] [culprit] [url] [message]"
    exit 1
fi

PROJECTNAME="${1}"
CULPRIT="${2}"
URL="${3}"
MESSAGE="${4}"

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

# The message to send
REPORT="[${PROJECTNAME}] ${MESSAGE}

in ${CULPRIT}

${URL}"

#REPORT="${REPORT//_/\_/}"

# AwesomeCorp dev groupchat
CHATID="-4242424242"
KEY="YOUR:KEY-HERE"
TIME="10"
URL="https://api.telegram.org/bot$KEY/sendMessage"

curl -s --max-time $TIME -d "chat_id=$CHATID&disable_web_page_preview=1&text=$REPORT" $URL >/dev/null
exit
curl -X "POST" "https://api.telegram.org/bot${KEY}/sendMessage" \
     -H "Content-Type: application/x-www-form-urlencoded; charset=utf-8" \
     --data-urlencode "text=${REPORT}" \
     --data-urlencode "chat_id=${CHATID}" \
     --data-urlencode "disable_web_page_preview=true" \
     --data-urlencode "parse_mode=markdown"
