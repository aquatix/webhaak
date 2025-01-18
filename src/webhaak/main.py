"""Main Webhaak application (runtime)."""

import binascii
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue

from webhaak import incoming, tasks

settings = tasks.settings


@asynccontextmanager
async def lifespan(the_app: FastAPI):
    """Upon start, initialise an AsyncClient and assign it to an attribute named requests_client on the app object."""
    the_app.requests_client = httpx.AsyncClient()
    yield
    await the_app.requests_client.aclose()


app = FastAPI(lifespan=lifespan)

logger = logging.getLogger('webhaak')
if settings.debug:
    logger.setLevel(logging.DEBUG)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # Allow requests from everywhere
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# Default job runtime
DEFAULT_TIMEOUT = 10 * 60


async def verify_key(secret_key: str):
    """Verify whether this endpoint contains the secret part in its URL."""
    try:
        if secret_key != settings.secretkey:
            logger.warning('Secret key incorrect trying to list triggers')
            raise HTTPException(status_code=404, detail='Secret key not found')
    except AttributeError as exc:
        logger.warning('Secret key not found trying to list triggers')
        raise HTTPException(status_code=404, detail='Secret key not found') from exc


# == Web app endpoints ======


@app.get('/')
async def indexpage():
    """Index page, just link to the project repo."""
    logger.info('Root page requested')
    return {
        'message': 'Welcome to Webhaak. See the documentation how to setup and use webhooks: '
        'https://github.com/aquatix/webhaak'
    }


@app.get('/admin/{secret_key}/list', dependencies=[Depends(verify_key)])
async def list_triggers(request: Request):
    """List the app_keys and trigger_keys available.

    :param Request request: fastAPI Request object to get headers from
    :return: json Response
    :rtype: dict
    """
    logger.info('Trigger list requested')

    server_url = request.base_url

    result = {}
    for project, project_info in tasks.projects.items():
        result[project] = {
            'title': project,
            'app_key': project_info['app_key'],
            'triggers': [],
        }
        for trigger in project_info['triggers']:
            url = f'{server_url}app/{project_info["app_key"]}/{project_info["triggers"][trigger]["trigger_key"]}'
            result[project]['triggers'].append(
                {
                    'title': trigger,
                    'trigger_key': project_info['triggers'][trigger]['trigger_key'],
                    'url': url,
                }
            )
    return {'projects': result}


@app.get('/app/{app_key}/{trigger_key}')
@app.options('/app/{app_key}/{trigger_key}')
@app.post('/app/{app_key}/{trigger_key}')
async def app_trigger(app_key: str, trigger_key: str, request: Request):
    """Fire the trigger described by the configuration under `trigger_key`.

    :param str app_key: application key part of the url
    :param str trigger_key: trigger key part of the url, sub part of the config
    :param Request request: fastAPI Request object to get headers from
    :return: json Response
    :rtype: dict
    """
    logger.info('%s on app_key: %s trigger_key: %s', request.method, app_key, trigger_key)
    config = tasks.get_trigger_settings(app_key, trigger_key)
    if config is None:
        logger.error('app_key/trigger_key combo not found')
        # return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')
        raise HTTPException(status_code=404, detail='Error')

    event_info = ''
    hook_info = {'event_type': 'push'}

    call_external_url = False
    rss_message = False
    sentry_message = False
    statuspage_message = False
    freshping_monitor = False

    if request.method == 'POST':
        if request.headers.get('X-Gitea-Event'):
            vcs_source = 'Gitea'
        elif request.headers.get('X-Gogs-Event'):
            vcs_source = 'Gogs'
        elif request.headers.get('X-GitHub-Event'):
            vcs_source = 'GitHub'
        elif request.headers.get('X-Event-Key'):
            # Other option is to check for User-Agent: Bitbucket-Webhooks/2.0
            vcs_source = 'BitBucket'
            # Examples: pullrequest:fulfilled pullrequest:created
            event_key = request.headers.get('X-Event-Key')
            logger.debug('BitBucket event: %s', event_key)
            if 'pullrequest:' in event_key:
                hook_info['pullrequest_status'] = request.headers.get('X-Event-Key').split(':')[1].strip()
                if hook_info['pullrequest_status'] == 'fulfilled':
                    hook_info['event_type'] = 'merge'
                elif hook_info['pullrequest_status'] == 'created':
                    hook_info['event_type'] = 'new'
        elif request.headers.get('Sentry-Trace'):
            logger.debug('Sentry webhook')
            sentry_message = True
            vcs_source = 'n/a'
        else:
            vcs_source = '<unknown>'

        hook_info['vcs_source'] = vcs_source

        try:
            payload = await request.json()
        except json.decoder.JSONDecodeError as error:
            payload = {}
            logger.error('JSON decode error: %s', error)

        logger.debug(payload)
        url = ''
        if payload:
            # Debug: dump payload to disk
            event_date = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            with open(f'{settings.eventlog_dir}/{event_date}_event.json', 'w', encoding='utf-8') as outfile:
                json.dump(payload, outfile)
            with open(f'{settings.eventlog_dir}/{event_date}_headers.json', 'w', encoding='utf-8') as outfile:
                json.dump(dict(request.headers.items()), outfile)

            if 'repository' in payload:
                if 'html_url' in payload['repository']:
                    url = payload['repository']['html_url']
                elif 'links' in payload['repository']:
                    # BitBucket
                    url = payload['repository']['links']['html']['href']
            elif payload.get('items') and payload['items'][0].get('canonical'):
                hook_info['event_type'] = 'news_item'
                rss_message = True
            elif payload.get('incident'):
                hook_info['event_type'] = 'statuspage_update'
                statuspage_message = True
            elif payload.get('check_url'):
                hook_info['event_type'] = 'freshping_monitor'
                freshping_monitor = True
        # Likely some ping was sent, check if so
        if request.headers.get('X-GitHub-Event') == 'ping':
            logger.info('received %s ping for %s hook: %s ', vcs_source, payload['repository']['full_name'], url)
            return json.dumps({'msg': 'Hi!'})
        if (
            request.headers.get('X-GitHub-Event') == 'push'
            or request.headers.get('X-Gitea-Event') == 'push'
            or request.headers.get('X-Gogs-Event') == 'push'
            or request.headers.get('X-Event-Key') == 'repo:push'
        ):
            event_info = f'received push from {vcs_source} for '
        elif rss_message:
            event_info = 'received RSS item for '
        elif sentry_message:
            event_info = 'received push from Sentry for '
        elif statuspage_message:
            event_info = 'received statuspage update for '
        elif freshping_monitor:
            event_info = 'received Freshping monitoring update for '
        elif 'call_url' in config[1]:
            call_external_url = True
            event_info = 'received external URL for '
        else:
            logger.info(
                'received wrong event type from %s for %s hook: %s',
                vcs_source,
                payload.get('repository', {}).get('full_name', 'not available'),
                url,
            )
            return {'error': 'wrong event type'}

        if call_external_url:
            # Event type is 'call another URL'
            status, response = await tasks.call_url(request, config)
            return {
                'status': status,
                'message': 'Command accepted and was passed on',
                'response': response,
            }

        if not payload:
            logger.error(
                '%s unknown, as no json was received. Check that %s webhook content type is application/json',
                str(event_info),
                vcs_source,
            )

        if rss_message:
            event_info = await incoming.handle_inoreader_rss_item(payload, hook_info, event_info)
        elif sentry_message:
            await incoming.handle_sentry_message(payload, hook_info, event_info)
            status, response = tasks.do_handle_sentry_message(config, hook_info)
            return {
                'status': status,
                'message': 'Command accepted and was passed on',
                'response': response,
            }
        elif statuspage_message:
            await incoming.handle_statuspage_update(payload, hook_info, event_info)
            status, response = tasks.do_handle_statuspage_message(config, hook_info)
            return {
                'status': status,
                'message': 'Command accepted and was passed on',
                'response': response,
            }
        elif freshping_monitor:
            event_info, hook_info = await incoming.handle_freshping(payload, hook_info, event_info)
            status, response = tasks.do_handle_freshping(config, hook_info)
            return {
                'status': status,
                'message': 'Command accepted and was passed on',
                'response': response,
            }
        else:
            event_info = await incoming.determine_task(config, payload, hook_info, event_info)

    # Create RQ job (task) for this request
    redis_conn = Redis()
    # use named queue to prevent clashes with other RQ workers
    q = Queue(connection=redis_conn, name='webhaak', default_timeout=DEFAULT_TIMEOUT)

    # Delay execution task, so it can run as its own process under RQ, synchronously
    if rss_message:
        job = q.enqueue(
            tasks.do_handle_inoreader_rss_message,
            args=(
                config,
                hook_info,
            ),
        )
    else:
        job = q.enqueue(
            tasks.do_pull_andor_command,
            args=(
                config,
                hook_info,
            ),
        )
    logger.info('Enqueued job with id: %s', job.id)

    if not os.path.isdir(settings.jobs_log_dir):
        os.makedirs(settings.jobs_log_dir)
    with open(os.path.join(settings.jobs_log_dir, f'{job.id}.log'), 'w', encoding='utf-8') as outfile:
        # Write event_info to task log
        outfile.write(event_info)

    server_url = request.base_url
    job.meta['job_url'] = f'{server_url}status/{job.id}'
    job.save_meta()
    return {
        'status': 'OK',
        'message': 'Command accepted and will be run in the background',
        'job_id': job.id,
        'url': f'{server_url}status/{job.id}',
    }


@app.get('/status/{job_id}')
async def job_status(job_id: str):
    """Show the status of job `job_id`.

    :param str job_id:
    :return: dictionary with a task `status` and a `result`, including a relevant `message` on failure
    :rtype: json
    """
    logger.info('Status requested for job %s', job_id)
    redis_conn = Redis()
    q = Queue(connection=redis_conn, name='webhaak')  # use named queue to prevent clashes with other RQ workers
    job = q.fetch_job(job_id)
    if job is None:
        response = {'status': 'unknown'}
    else:
        log_contents = ''
        job_logfile_name = os.path.normpath(os.path.join(settings.jobs_log_dir, f'{job_id}.log'))
        if os.path.isfile(job_logfile_name):
            with open(job_logfile_name, 'r', encoding='utf-8') as infile:
                log_contents = infile.readlines()
        job_result = job.latest_result()
        job_result_response = 'unknown, might still be running/waiting'
        if job_result:
            job_result_response = job_result.type.name
        response = {
            'status': job.get_status(),
            'result': job_result_response,
            'log': log_contents,
        }
        if not job_result:
            response['message'] = ''
        elif job_result == job_result.Type.SUCCESSFUL:
            response['message'] = job_result.return_value
        else:
            response['message'] = job_result.exc_string
        if response['message']:
            response['message'] = response['message'].strip().split('\n')
    return response


@app.get('/monitor/monitor.html')
@app.get('/monitor/')
@app.get('/monitor')
async def monitor():
    """Respond to monitoring ping."""
    return 'OK'


def generate_key():
    """Generate a random ascii string to be used as identifier."""
    return binascii.hexlify(os.urandom(24))


@app.get('/admin/{secret_key}/get_app_key', dependencies=[Depends(verify_key)])
async def get_app_key():
    """Generate new app_key."""
    logger.info('New key requested through get_app_key')
    return {'key': generate_key().decode('utf-8')}
