import binascii
import json
import logging
import os

import strictyaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from rq import Queue
from strictyaml import Bool, Map, MapPattern, Optional, Str

from core import tasks

app = FastAPI()
DEBUG = os.getenv("DEBUG", "False")
SECRETKEY = os.getenv("SECRETKEY", "")
print(f"SECRETKEY: {SECRETKEY}")

logger = logging.getLogger('webhaak')
if DEBUG.lower() in ('true'):
    logger.setLevel(logging.DEBUG)
# Log will rotate daily with a max history of LOG_BACKUP_COUNT
#  fh = logging.FileHandler(
#      settings.LOG_LOCATION
#  )
#  fh.setLevel(logging.DEBUG)
#  formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#  fh.setFormatter(formatter)
#  logger.addHandler(fh)

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

    server_url = request.host_url

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
                    'url': '{}app/{}/{}'.format(
                        server_url,
                        project_info['appkey'],
                        project_info['triggers'][trigger]['triggerkey']
                    )
                }
            )
    return {'projects': result}


#  @app.route('/app/{appkey}/{triggerkey}', methods=['GET', 'OPTIONS', 'POST'])
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
        payload = request.get_json()
        logger.debug(payload)
        url = ''
        if payload:
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
            return json.dumps({'msg': "wrong event type"})
        if payload:
            if 'push' in payload:
                # BitBucket, which has a completely different format
                logger.debug('Amount of changes in this push: %d', len(payload['push']['changes']))
                hook_info['commit_before'] = None  # When a branch is created, old is null; use as default
                # Only take info from the first change item
                if payload['push']['changes'][0]['old']:
                    # Info on the previous commit is available (so not a new branch)
                    hook_info['commit_before'] = payload['push']['changes'][0]['old']['target']['hash']
                hook_info['commit_after'] = payload['push']['changes'][0]['new']['target']['hash']
                hook_info['compare_url'] = payload['push']['changes'][0]['links']['html']['href']

                hook_info['commits'] = []
                for commit in payload['push']['changes'][0]['commits']:
                    commit_info = {'hash': commit['hash']}
                    if 'user' in commit['author']:
                        if 'username' in commit['author']['user']:
                            commit_info['name'] = commit['author']['user']['username']
                        else:
                            commit_info['name'] = commit['author']['user']['nickname']
                    commit_info['email'] = commit['author']['raw']
                    hook_info['commits'].append(commit_info)

            if 'pullrequest' in payload:
                # BitBucket pullrequest event
                if 'rendered' in payload['pullrequest']:
                    hook_info['pullrequest_title'] = payload['pullrequest']['rendered']['title']['raw']
                    hook_info['pullrequest_description'] = payload['pullrequest']['rendered']['description']['raw']
                if 'close_source_branch' in payload['pullrequest']:
                    hook_info['pullrequest_close_source_branch'] = payload['pullrequest']['close_source_branch']
                if 'state' in payload['pullrequest']:
                    if payload['pullrequest']['state'] == 'MERGED':
                        hook_info['pullrequest_author'] = payload['pullrequest']['author']['display_name']
                        hook_info['pullrequest_closed_by'] = payload['pullrequest']['closed_by']['display_name']
                if 'links' in payload['pullrequest'] and 'html' in payload['pullrequest']['links']:
                    hook_info['pullrequest_url'] = payload['pullrequest']['links']['html']

            if 'ref' in payload:
                hook_info['ref'] = payload['ref']
                if 'heads' in payload['ref']:
                    hook_info['branch'] = payload['ref'].replace('refs/heads/', '')
                elif 'tags' in payload['ref']:
                    hook_info['tag'] = payload['ref'].replace('refs/tags/', '')
            if 'repository' in payload:
                event_info += payload['repository']['full_name']
                hook_info['reponame'] = payload['repository']['full_name']
                if 'name' in payload['repository']:
                    hook_info['project_name'] = payload['repository']['name']
            if 'actor' in payload:
                # BitBucket pusher; no email address known here though
                event_info += ' by ' + payload['actor']['nickname']
                if 'display_name' in payload['actor']:
                    event_info += ' ({})'.format(payload['actor']['display_name'])
                hook_info['username'] = payload['actor']['nickname']

                logger.debug(config[1])
                if 'authors' in config[1]:
                    # Look up the email address in the known authors list of the project
                    for author in config[1]['authors']:
                        if author.lower() == hook_info['username'].lower():
                            hook_info['email'] = config[1]['authors'][author]
                            break
            if 'pusher' in payload:
                if vcs_source in ('Gitea', 'Gogs'):
                    event_info += ' by ' + payload['pusher']['username']
                    hook_info['username'] = payload['pusher']['username']
                    hook_info['email'] = payload['pusher']['email']
                elif vcs_source == 'GitHub':
                    event_info += ' by ' + payload['pusher']['name']
                    hook_info['username'] = payload['pusher']['name']
                    hook_info['email'] = payload['pusher']['email']
            if 'compare' in payload:
                event_info += ', compare: ' + payload['compare']
                hook_info['compare_url'] = payload['compare']
            elif 'compare_url' in payload:
                # GitHub, gitea, gogs
                event_info += ', compare: ' + payload['compare_url']
                hook_info['compare_url'] = payload['compare_url']
            if 'before' in payload:
                hook_info['commit_before'] = payload['before']
            if 'after' in payload:
                hook_info['commit_after'] = payload['after']
            if 'commits' in payload:
                # Gather info on the commits included in this push
                hook_info['commits'] = []
                for commit in payload['commits']:
                    commit_info = {}
                    if 'sha' in commit:
                        commit_info['hash'] = commit['sha']
                    elif 'id' in commit:
                        commit_info['hash'] = commit['id']
                    if 'author' in commit:
                        commit_info['name'] = commit['author']['name']
                        commit_info['email'] = commit['author']['email']
                    hook_info['commits'].append(commit_info)
            if sentry_message:
                event_info += payload['project_name']
                sentry_fields = ['project_name', 'culprit', 'url', 'message']
                for field in sentry_fields:
                    if field in payload:
                        hook_info[field] = payload[field]
                hook_info['stacktrace'] = 'Not available'
                if 'event' in payload and payload['event'] and 'title' in payload['event']:
                    hook_info['title'] = payload['event']['title']
                    stacktrace = []
                    if 'exception' in payload['event']:
                        # Always take the last set
                        frames = payload['event']['exception']['values'][-1]['stacktrace']['frames']
                        for frame in frames:
                            frame_message = '*{}* in *{}* at line *{}*'.format(
                                frame['filename'],
                                frame['function'],
                                frame['lineno']
                            )
                            stacktrace.append(frame_message)
                        # Sentry puts the items of the trace from last to first in the json, so reverse the trace
                        stacktrace.reverse()
                    elif 'logentry' in payload['event']:
                        if 'message' in payload['event']['logentry']:
                            stacktrace.append(payload['event']['logentry']['message'])
                        if 'formatted' in payload['event']['logentry']:
                            stacktrace.append(payload['event']['logentry']['formatted'])
                    logger.debug(stacktrace)
                    hook_info['stacktrace'] = '\\n'.join(stacktrace)
        else:
            '{}unknown, as no json was received. Check that {} webhook content type is application/json'.format(
                event_info,
                vcs_source
            )
        logger.debug(hook_info)
        logger.info(event_info)

    # Create RQ job (task) for this request
    redis_conn = Redis()
    q = Queue(connection=redis_conn)  # no args implies the default queue

    # Delay execution of count_words_at_url('http://nvie.com')
    job = q.enqueue(tasks.do_pull_andor_command, args=(config, hook_info,))
    logger.info('Enqueued job with id: %s', job.id)
    return {
        'status': 'OK',
        'message': 'Command accepted and will be run in the background',
        'job_id': job.id,
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
        response = {
            'status': job.get_status(),
            'result': job.result,
        }
        if job.is_failed:
            response['message'] = job.exc_info.strip().split('\n')[-1]
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
