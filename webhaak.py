import binascii
import json
import logging
import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue

from core import incoming, tasks

app = FastAPI()
DEBUG = os.getenv("DEBUG", "False")
# SECRETKEY should be set, it is mandatory. Please set it as env var
SECRETKEY = os.environ['SECRETKEY']
print(f"SECRETKEY: {SECRETKEY}")

# Get log output dir for payloads from environment; default is current working dir
LOG_DIR = os.getenv('LOG_DIR', os.getcwd())
EVENTLOG_DIR = os.getenv('EVENTLOG_DIR', os.getcwd())
JOBSLOG_DIR = os.path.join(LOG_DIR, 'jobs')

logger = logging.getLogger('webhaak')
if DEBUG.lower() in ('true'):
    logger.setLevel(logging.DEBUG)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from everywhere
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# == Web app endpoints ======

@app.get('/')
async def indexpage():
    logger.debug('Root page requested')
    return {
        'message': 'Welcome to Webhaak. See the documentation how to setup and use webhooks: '
        'https://github.com/aquatix/webhaak'
    }


@app.get('/admin/{secretkey}/list')
async def listtriggers(secretkey: str, request: Request):
    """List the appkeys and triggerkeys"""
    logger.debug('Trigger list requested')
    try:
        if secretkey != SECRETKEY:
            logger.debug('Secret key incorrect trying to list triggers')
            raise HTTPException(status_code=404, detail="Secret key not found")
    except AttributeError as exc:
        logger.debug('Secret key not found trying to list triggers')
        raise HTTPException(status_code=404, detail="Secret key not found") from exc

    server_url = request.base_url

    result = {}
    for project, project_info in tasks.projects.items():
        result[project] = {
            'title': project,
            'appkey': project_info['appkey'],
            'triggers': [],
        }
        for trigger in project_info['triggers']:
            result[project]['triggers'].append(
                {
                    'title': trigger,
                    'triggerkey': project_info['triggers'][trigger]['triggerkey'],
                    'url': f"{server_url}app/{project_info['appkey']}/{project_info['triggers'][trigger]['triggerkey']}"
                }
            )
    return {'projects': result}


@app.get('/app/{appkey}/{triggerkey}')
@app.options('/app/{appkey}/{triggerkey}')
@app.post('/app/{appkey}/{triggerkey}')
async def apptrigger(appkey: str, triggerkey: str, request: Request):
    """Fire the trigger described by the configuration under `triggerkey`

    :param appkey: application key part of the url
    :param triggerkey: trigger key part of the url, sub part of the config
    :return: json Response
    """
    logger.info('%s on appkey: %s triggerkey: %s', request.method, appkey, triggerkey)
    config = tasks.get_trigger_settings(appkey, triggerkey)
    if config is None:
        logger.error('appkey/triggerkey combo not found')
        # return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')
        raise HTTPException(status_code=404, detail="Error")

    event_info = ''
    hook_info = {}
    hook_info['event_type'] = 'push'
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
            eventdate = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            with open(f'{EVENTLOG_DIR}/{eventdate}_event.json', 'w', encoding='utf-8') as outfile:
                json.dump(payload, outfile)
            with open(f'{EVENTLOG_DIR}/{eventdate}_headers.json', 'w', encoding='utf-8') as outfile:
                json.dump({k: v for k, v in request.headers.items()}, outfile)

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

        # Debug: dump payload to disk
        # eventdate = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        # with open(f'log/webhaak_events/{eventdate}_event.json', 'w') as outfile:
        #    json.dump(payload, outfile)
        # with open(f'log/webhaak_events/{eventdate}_headers.json', 'w') as outfile:
        #    json.dump({k:v for k, v in request.headers.items()}, outfile)

        event_info = incoming.determine_task(config, payload, hook_info, event_info)
        # Write event_info to task log

    # Create RQ job (task) for this request
    redis_conn = Redis()
    q = Queue(connection=redis_conn)  # no args implies the default queue

    # Delay execution of count_words_at_url('http://nvie.com')
    job = q.enqueue(tasks.do_pull_andor_command, args=(config, hook_info,))
    logger.info('Enqueued job with id: %s', job.id)

    if not os.path.isdir(JOBSLOG_DIR):
        os.makedirs(JOBSLOG_DIR)
    with open(os.path.join(JOBSLOG_DIR, f'{job.id}.log'), 'w', encoding='utf-8') as outfile:
        outfile.write(event_info)

    server_url = request.base_url
    return {
        'status': 'OK',
        'message': 'Command accepted and will be run in the background',
        'job_id': job.id,
        'url': f'{server_url}status/{job.id}'
    }


@app.get('/status/{job_id}')
async def job_status(job_id):
    """Show the status of job `job_id`

    :param str job_id:
    :return: dictionary with a task `status` and a `result`, including a relevant `message` on failure
    :rtype: json
    """
    redis_conn = Redis()
    q = Queue(connection=redis_conn)  # no args implies the default queue
    job = q.fetch_job(job_id)
    if job is None:
        response = {'status': 'unknown'}
    else:
        log_contents = ''
        job_logfile_name = os.path.join(JOBSLOG_DIR, f'{job_id}.log')
        if os.path.isfile(job_logfile_name):
            with open(job_logfile_name, 'r', encoding='utf-8') as infile:
                log_contents = infile.readlines()
        response = {
            'status': job.get_status(),
            'result': job.result,
            'log': log_contents,
        }
        if job.is_failed:
            response['message'] = job.exc_info.strip().split('\n')
    return response


@app.get('/monitor/monitor.html')
@app.get('/monitor/')
@app.get('/monitor')
async def monitor():
    """Monitoring ping"""
    result = 'OK'
    return result


def generatekey():
    """Generate a random ascii string to be used as identifier"""
    return binascii.hexlify(os.urandom(24))


#  @app.cli.command()
#  def printappkey():
#      """Generate new appkey"""
#      print(generatekey())


@app.get('/getappkey')
async def getappkey():
    """Generate new appkey"""
    return {'key': generatekey().decode('utf-8')}
