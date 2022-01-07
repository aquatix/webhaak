import binascii
import json
import logging
import os
from datetime import timedelta
from functools import update_wrapper

import strictyaml
from flask import (Flask, Response, abort, current_app, jsonify, make_response,
                   request)
from redis import Redis
from rq import Queue
from strictyaml import Bool, Map, MapPattern, Optional, Str

import tasks
from tasks import settings

app = Flask(__name__)
app.debug = settings.DEBUG

app.logger.setLevel(logging.DEBUG)
# Log will rotate daily with a max history of LOG_BACKUP_COUNT
fh = logging.FileHandler(
    settings.LOG_LOCATION
)
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
app.logger.addHandler(fh)

# strictyaml schema for project settings
schema = MapPattern(
    Str(),
    Map(
        {
            "appkey": Str(),
            "triggers": MapPattern(Str(), Map({
                "triggerkey": Str(),
                Optional("notify"): Bool(),
                Optional("notify_on_error"): Bool(),
                Optional("repo"): Str(),
                Optional("repoparent"): Str(),
                Optional("branch"): Str(),
                Optional("command"): Str(),
                Optional("authors"): MapPattern(Str(), Str()),
            }))
        }
    )
)

# Load the configuration of the various projects/hooks
with open(settings.PROJECTS_FILE, 'r') as pf:
    projects = strictyaml.load(pf.read(), schema).data


# == API request support functions/mixins ======

def crossdomain(origin=None, methods=None, headers=None,
                max_age=21600, attach_to_all=True,
                automatic_options=True):
    """Decorator to send the correct cross-domain headers
    src: https://blog.skyred.fi/articles/better-crossdomain-snippet-for-flask.html
    """
    if methods is not None:
        methods = ', '.join(sorted(x.upper() for x in methods))
    if headers is not None and not isinstance(headers, str):
        headers = ', '.join(x.upper() for x in headers)
    if not isinstance(origin, str):
        origin = ', '.join(origin)
    if isinstance(max_age, timedelta):
        max_age = max_age.total_seconds()

    def get_methods():
        if methods is not None:
            return methods

        options_resp = current_app.make_default_options_response()
        return options_resp.headers['allow']

    def decorator(f):
        def wrapped_function(*args, **kwargs):
            if automatic_options and request.method == 'OPTIONS':
                resp = current_app.make_default_options_response()
            else:
                resp = make_response(f(*args, **kwargs))
            if not attach_to_all and request.method != 'OPTIONS':
                return resp

            h = resp.headers
            h['Access-Control-Allow-Origin'] = origin
            h['Access-Control-Allow-Methods'] = get_methods()
            h['Access-Control-Max-Age'] = str(max_age)
            h['Access-Control-Allow-Credentials'] = 'true'
            h['Access-Control-Allow-Headers'] = \
                "Origin, X-Requested-With, Content-Type, Accept, Authorization"
            if headers is not None:
                h['Access-Control-Allow-Headers'] = headers
            return resp

        f.provide_automatic_options = False
        return update_wrapper(wrapped_function, f)
    return decorator


class APIException(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        rv['status_code'] = self.status_code
        return rv


class InvalidAPIUsage(APIException):
    status_code = 400


@app.errorhandler(404)
def page_not_found(e):
    return jsonify(error=404, text=str(e)), 404


@app.errorhandler(InvalidAPIUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    response.mimetype = 'application/json'
    return response


# == Web app endpoints ======

@app.route('/')
def indexpage():
    app.logger.debug('Root page requested')
    return 'Welcome to <a href="https://github.com/aquatix/webhaak">Webhaak</a>, see the documentation to how to setup and use webhooks.'


@app.route('/admin/<secretkey>/list', methods=['GET'])
@crossdomain(origin='*')
def listtriggers(secretkey):
    """List the appkeys and triggerkeys"""
    app.logger.debug('Trigger list requested')
    try:
        if secretkey != settings.SECRETKEY:
            app.logger.debug('Secret key incorrect trying to list triggers')
            abort(404)
    except AttributeError:
        app.logger.debug('Secret key not found trying to list triggers')
        abort(404)

    server_url = request.host_url

    result = {}
    for project in projects:
        result[project] = {
            'title': project,
            'appkey': projects[project]['appkey'],
            'triggers': [],
        }
        for trigger in projects[project]['triggers']:
            result[project]['triggers'].append(
                {
                    'title': trigger,
                    'triggerkey': projects[project]['triggers'][trigger]['triggerkey'],
                    'url': '{}app/{}/{}'.format(
                        server_url,
                        projects[project]['appkey'],
                        projects[project]['triggers'][trigger]['triggerkey']
                    )
                }
            )
    return Response(
        json.dumps({'projects': result}), status=200, mimetype='application/json'
    )


@app.route('/app/<appkey>/<triggerkey>', methods=['GET', 'OPTIONS', 'POST'])
@crossdomain(origin='*')
def apptrigger(appkey, triggerkey):
    """Fire the trigger described by the configuration under `triggerkey`

    :param appkey: application key part of the url
    :param triggerkey: trigger key part of the url, sub part of the config
    :return: json Response
    """
    app.logger.info('%s on appkey: %s triggerkey: %s', request.method, appkey, triggerkey)
    config = tasks.get_trigger_settings(appkey, triggerkey)
    if config is None:
        app.logger.error('appkey/triggerkey combo not found')
        return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')

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
            app.logger.debug('BitBucket event: %s', event_key)
            if 'pullrequest:' in event_key:
                hook_info['pullrequest_status'] = request.headers.get('X-Event-Key').split(':')[1].strip()
                if hook_info['pullrequest_status'] == 'fulfilled':
                    hook_info['event_type'] = 'merge'
                elif hook_info['pullrequest_status'] == 'created':
                    hook_info['event_type'] = 'new'
        elif request.headers.get('Sentry-Trace'):
            app.logger.debug('Sentry webhook')
            sentry_message = True
            vcs_source = 'n/a'
        else:
            vcs_source = '<unknown>'

        hook_info['vcs_source'] = vcs_source
        payload = request.get_json()
        app.logger.debug(payload)
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
            app.logger.info(
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
            event_info = 'received push from {} for '.format(vcs_source)
        elif sentry_message:
            event_info = 'received push from Sentry for '
        else:
            app.logger.info(
                'received wrong event type from %s for %s hook: %s',
                vcs_source,
                payload['repository']['full_name'],
                url
            )
            return json.dumps({'msg': "wrong event type"})
        if payload:
            if 'push' in payload:
                # BitBucket, which has a completely different format
                app.logger.debug('Amount of changes in this push: %d', len(payload['push']['changes']))
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

                app.logger.debug(config[1])
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
                    app.logger.debug(stacktrace)
                    hook_info['stacktrace'] = '\\n'.join(stacktrace)
        else:
            '{}unknown, as no json was received. Check that {} webhook content type is application/json'.format(
                event_info,
                vcs_source
            )
        app.logger.debug(hook_info)
        app.logger.info(event_info)

    #  print(config)
    #  print('---')
    #  print(hook_info)
    #  print('------')

    # Create RQ job (task) for this request
    redis_conn = Redis()
    q = Queue(connection=redis_conn)  # no args implies the default queue

    # Delay execution of count_words_at_url('http://nvie.com')
    # job = q.enqueue(tasks.do_pull_andor_command, args=(config, hook_info,))
    job = q.enqueue("webhaak.tasks.do_pull_andor_command", args=(config, hook_info,))
    app.logger.info('Enqueued job with id: %s' % job.id)
    return Response(
        json.dumps({
            'status': 'OK',
            'message': 'Command accepted and will be run in the background',
            'job_id': job.id,
        }), status=200, mimetype='application/json'
    )


@app.route('/status/<job_id>')
def job_status(job_id):
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
    return jsonify(response)


@app.route('/monitor/monitor.html')
@app.route('/monitor/')
@app.route('/monitor')
def monitor():
    """Monitoring ping"""
    result = 'OK'
    return result


def generatekey():
    """Generate a random ascii string to be used as identifier"""
    return binascii.hexlify(os.urandom(24))


@app.cli.command()
def printappkey():
    """Generate new appkey"""
    print(generatekey())


@app.route('/getappkey')
def getappkey():
    """Generate new appkey"""
    return Response(json.dumps({'key': generatekey().decode('utf-8')}), status=200, mimetype='application/json')


if __name__ == '__main__':
    if not settings.DEBUG:
        app.run(port=settings.PORT, debug=settings.DEBUG)
    else:
        app.run(host='0.0.0.0', port=settings.PORT, debug=settings.DEBUG)
