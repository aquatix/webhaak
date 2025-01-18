import json

from aiohttp.web import Request
from markdown_it import MarkdownIt
from opsdroid.events import Message
from opsdroid.matchers import match_webhook
from opsdroid.skill import Skill


class OpsdroidBotHookSkill(Skill):
    def format_as_html(self, markdown_message):
        """Format the (markdown formatted) text to HTML for Matrix"""
        md = MarkdownIt('commonmark', {'breaks': True, 'html': True}).enable('table')
        # Ensure the message is a string, and strip leading and trailing whitespace
        message = str(markdown_message).strip()

        return md.render(message)

    @match_webhook('echo')
    async def echowebhook(self, event: Request):
        # Capture the post data
        data = await event.text()

        # Respond with the data in the default room on the default connector
        message = self.format_as_html(data)
        await self.opsdroid.send(Message(message))

    @match_webhook('notification')
    async def notificationwebhookskill(self, event: Request):
        # Capture the post data
        try:
            data = await event.json()

            if 'title' in data:
                message = f'{data["title"]}: {data["message"]}'
            else:
                message = data['message']

            if 'url' in data:
                # Append the source URL if there is any
                message = f'{message}\n\n[source]({data["url"]})'
        except json.JSONDecodeError:
            message = await event.text()

        # Respond with the data in the default room on the default connector
        message = self.format_as_html(data)
        await self.opsdroid.send(Message(message))
