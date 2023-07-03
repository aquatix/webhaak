import binascii
import json
import logging
import os
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue

from webhaak import incoming, tasks

settings = tasks.settings

app = FastAPI()

logger = logging.getLogger('webhaak')
if settings.debug:
    logger.setLevel(logging.DEBUG)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from everywhere
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_key(secret_key: str):
    """Verify whether this endpoint contains the secret part in its URL"""
    try:
        if secret_key != settings.secretkey:
            logger.warning('Secret key incorrect trying to list triggers')
            raise HTTPException(status_code=404, detail="Secret key not found")
    except AttributeError as exc:
        logger.warning('Secret key not found trying to list triggers')
        raise HTTPException(status_code=404, detail="Secret key not found") from exc


# == Web app endpoints ======

@app.get('/')
async def indexpage():
    """Index page, just link to the project repo"""
    logger.info('Root page requested')
    return {
        'message': 'Welcome to Webhaak. See the documentation how to setup and use webhooks: '
        'https://github.com/aquatix/webhaak'
    }


@app.get('/admin/{secret_key}/list', dependencies=[Depends(verify_key)])
async def list_triggers(request: Request):
    """List the app_keys and trigger_keys available"""
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
            result[project]['triggers'].append(
                {
                    'title': trigger,
                    'trigger_key': project_info['triggers'][trigger]['trigger_key'],
                    'url':
                        f"{server_url}app/{project_info['app_key']}/{project_info['triggers'][trigger]['trigger_key']}"
                }
            )
    return {'projects': result}


@app.get('/app/{app_key}/{trigger_key}')
@app.options('/app/{app_key}/{trigger_key}')
@app.post('/app/{app_key}/{trigger_key}')
async def app_trigger(app_key: str, trigger_key: str, request: Request):
    """Fire the trigger described by the configuration under `trigger_key`

    :param str app_key: application key part of the url
    :param str trigger_key: trigger key part of the url, sub part of the config
    :param Request request: fastAPI Request object to get headers from
    :return: json Response
    """
    logger.info('%s on app_key: %s trigger_key: %s', request.method, app_key, trigger_key)
    config = tasks.get_trigger_settings(app_key, trigger_key)
    if config is None:
        logger.error('app_key/trigger_key combo not found')
        # return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')
        raise HTTPException(status_code=404, detail="Error")

    event_info = ''
    hook_info = {'event_type': 'push'}
    sentry_message = False
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
        payload = await request.json()
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
        # Likely some ping was sent, check if so
        if request.headers.get('X-GitHub-Event') == "ping":
            logger.info(
                'received %s ping for %s hook: %s ',
                vcs_source,
                payload['repository']['full_name'],
                url
            )
            return json.dumps({'msg': 'Hi!'})
        if (
            request.headers.get('X-GitHub-Event') == "push"
            or request.headers.get('X-Gitea-Event') == "push"
            or request.headers.get('X-Gogs-Event') == "push"
            or request.headers.get('X-Event-Key') == "repo:push"
        ):
            event_info = f'received push from {vcs_source} for '
        elif sentry_message:
            event_info = 'received push from Sentry for '
        else:
            logger.info(
                'received wrong event type from %s for %s hook: %s',
                vcs_source,
                payload['repository']['full_name'],
                url
            )
            return {'error': "wrong event type"}

        if not payload:
            logger.error(
                '%s unknown, as no json was received. Check that %s webhook content type is application/json',
                str(event_info),
                vcs_source
            )

        if sentry_message:
            event_info = incoming.handle_sentry_message(payload, hook_info, event_info)
        else:
            event_info = incoming.determine_task(config, payload, hook_info, event_info)
        # Write event_info to task log

    # Create RQ job (task) for this request
    redis_conn = Redis()
    q = Queue(connection=redis_conn, queue='webhaak')  # use named queue to prevent clashes with other RQ workers

    # Delay execution of count_words_at_url('http://nvie.com')
    job = q.enqueue(tasks.do_pull_andor_command, args=(config, hook_info,))
    logger.info('Enqueued job with id: %s', job.id)

    if not os.path.isdir(settings.jobs_log_dir):
        os.makedirs(settings.jobs_log_dir)
    with open(os.path.join(settings.jobs_log_dir, f'{job.id}.log'), 'w', encoding='utf-8') as outfile:
        outfile.write(event_info)

    server_url = request.base_url
    return {
        'status': 'OK',
        'message': 'Command accepted and will be run in the background',
        'job_id': job.id,
        'url': f'{server_url}status/{job.id}'
    }


@app.get('/status/{job_id}')
async def job_status(job_id: str):
    """Show the status of job `job_id`

    :param str job_id:
    :return: dictionary with a task `status` and a `result`, including a relevant `message` on failure
    :rtype: json
    """
    logger.info('Status requested for job %s', job_id)
    redis_conn = Redis()
    q = Queue(connection=redis_conn, queue='webhaak')  # use named queue to prevent clashes with other RQ workers
    job = q.fetch_job(job_id)
    if job is None:
        response = {'status': 'unknown'}
    else:
        log_contents = ''
        job_logfile_name = os.path.join(settings.jobs_log_dir, f'{job_id}.log')
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
    """Monitoring ping"""
    return 'OK'


def generate_key():
    """Generate a random ascii string to be used as identifier"""
    return binascii.hexlify(os.urandom(24))


@app.get('/admin/{secret_key}/get_app_key', dependencies=[Depends(verify_key)])
async def get_app_key():
    """Generate new app_key"""
    logger.info('New key requested through get_app_key')
    return {'key': generate_key().decode('utf-8')}
