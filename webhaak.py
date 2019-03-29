import binascii
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from functools import update_wrapper
from logging.handlers import TimedRotatingFileHandler
from multiprocessing import Process

import git
import pushover
import strictyaml
from flask import (Flask, Response, abort, current_app, jsonify, make_response,
                   request)
from strictyaml import Bool, Map, MapPattern, Optional, Str

import settings

app = Flask(__name__)
app.debug = settings.DEBUG

logger = logging.getLogger('webhaak')
logger.setLevel(logging.DEBUG)
# Log will rotate daily with a max history of LOG_BACKUP_COUNT
fh = TimedRotatingFileHandler(
    settings.LOG_LOCATION,
    when='d',
    interval=1,
    backupCount=settings.LOG_BACKUP_COUNT
)
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

# strictyaml schema for project settings
schema = MapPattern(
    Str(),
    Map(
        {
            "appkey": Str(),
            "triggers": MapPattern(Str(), Map({
                "triggerkey": Str(),
                Optional("notify"): Bool(),
                Optional("repo"): Str(),
                Optional("repoparent"): Str(),
                Optional("repo_branch"): Str(),
                Optional("command"): Str(),
                Optional("authors"): MapPattern(Str(), Str()),
            }))
        }
    )
)

# Load the configuration of the various projects/hooks
with open(settings.PROJECTS_FILE, 'r') as pf:
    projects = strictyaml.load(pf.read(), schema).data


def notify_user(result, config):
    """Send a PushOver message if configured, after git and command have run

    result is a dictionary with fields:
      command_result
      status: 'OK' | 'error'
      type: 'commanderror'
      message
    """
    try:
        triggerconfig = config[1]
        projectname = '{}>{}'.format(config[0], triggerconfig['title'])
        title = ''
        branch = 'master'
        command = 'n/a'
        repo = 'n/a'
        if 'command' in triggerconfig:
            command = triggerconfig['command']
        if 'branch' in triggerconfig:
            branch = triggerconfig['branch']
        if 'repo' in triggerconfig:
            repo = triggerconfig['repo']
        message = 'repo: {}\nbranch: {}\ncommand: {}\nruntime: {}'.format(
            repo,
            branch,
            command,
            result['runtime']
        )
        if result['status'] == 'OK':
            title = "Hook for {} ran successfully".format(projectname)
        else:
            title = "Hook for {} failed: {}".format(projectname, result['type'])
            message = message + '\n\n{}'.format(result['message'])
        logging.debug(message)
        logging.info('Sending notification...')
        # TODO: option to send to Telegram chat
        client = pushover.Client(settings.PUSHOVER_USERKEY, api_token=settings.PUSHOVER_APPTOKEN)
        client.send_message(message, title=title)
        logging.info('Notification sent')
    except AttributeError:
        logging.warning('Notification through PushOver failed because of missing configuration')


def get_trigger_settings(appkey, triggerkey):
    """Look up the trigger and return the repo and command to be updated and fired

    :param appkey: application key part of the url
    :param triggerkey: trigger key part of the url, sub part of the config
    :return: tuple with project info and the trigger config
    """
    for project in projects:
        if projects[project]['appkey'] == appkey:
            for trigger in projects[project]['triggers']:
                if projects[project]['triggers'][trigger]['triggerkey'] == triggerkey:
                    triggerconfig = projects[project]['triggers'][trigger]
                    triggerconfig['title'] = trigger
                    return (project, triggerconfig)
    return None


def get_repo_basename(repo_url):
    """Extract repository basename from its url, as that will be the name of directory it will be cloned into"""
    result = os.path.basename(repo_url)
    filename, file_extension = os.path.splitext(result)
    if file_extension == '.git':
        # Strip the .git from the name, as Git will do the same on non-bare checkouts
        result = filename
    return result


def get_repo_version(repo_dir):
    """Gets version of Git repo, based on latest tag, number of commits since, and latest commit hash

    :param repo_dir: path to the Git repository
    :return: string with version
    """
    # Make sure the working directory is our project
    try:
        version = subprocess.check_output(["git", "describe", "--always", "--tags"], stderr=None, cwd=repo_dir).strip()
    except subprocess.CalledProcessError:
        version = ''

    try:
        # byte string needs to be converted to a string
        version = version.decode("utf-8")
    except AttributeError:
        # version already was a str
        pass
    return version


def fetchinfo_to_str(fetchinfo):
    """git.remote.FetchInfo to human readable representation"""
    result = fetchinfo[0].note
    return result


def update_repo(config):
    """Update (pull) the Git repo"""
    projectname = config[0]
    triggerconfig = config[1]

    repo_url = triggerconfig['repo']
    repo_parent = settings.REPOS_CACHE_DIR
    if 'repoparent' in triggerconfig and triggerconfig['repoparent']:
        repo_parent = triggerconfig['repoparent']

    logger.info('[%s] Updating %s', projectname, repo_url)
    logger.info('[%s] Repo parent %s', projectname, repo_parent)

    # Ensure cache dir for webhaak exists and is writable
    if not os.path.exists(repo_parent):
        os.makedirs(repo_parent)  # throws OSError if repo_parent is not writable

    # TODO: check whether dir exists with different repository
    repo_dir = os.path.join(repo_parent, get_repo_basename(repo_url))
    logger.info('[%s] Repo dir %s', projectname, repo_dir)
    if os.path.isdir(repo_dir):
        # Repo already exists locally, do a pull
        logger.info('[%s] Repo exists, pull', projectname)

        apprepo = git.Repo(repo_dir)
        origin = apprepo.remote('origin')
        result = fetchinfo_to_str(origin.fetch())  # assure we actually have data. fetch() returns useful information
        logger.info('[%s] Fetch result: %s', projectname, result)
    else:
        # Repo needs to be cloned
        logger.info('[%s] Repo does not exist yet, clone', projectname)
        apprepo = git.Repo.init(repo_dir)
        origin = apprepo.create_remote('origin', repo_url)
        origin.fetch()                  # assure we actually have data. fetch() returns useful information
        # Setup a local tracking branch of a remote branch
        apprepo.create_head('master', origin.refs.master).set_tracking_branch(origin.refs.master)
    branch = 'master'
    if 'branch' in triggerconfig:
        branch = triggerconfig['branch']
    logger.info('[%s] checkout() branch \'%s\'', projectname, branch)
    result = str(apprepo.git.checkout(branch))
    # pull (so really update) the checked out branch to latest commit
    origin.pull()
    logger.info('[%s] Done pulling branch \'%s\'', projectname, branch)
    return result


def run_command(config, hook_info):
    """Run the command(s) defined for this trigger"""
    projectname = config[0]
    triggerconfig = config[1]
    if 'command' not in triggerconfig:
        # No command to execute, return
        logger.info('[%s] No command to execute', projectname)
        return None
    command = triggerconfig['command']
    # Replace some placeholders to be used in executing scripts from one of the repos
    repo_parent = settings.REPOS_CACHE_DIR
    if 'repoparent' in triggerconfig and triggerconfig['repoparent']:
        repo_parent = triggerconfig['repoparent']
    if 'repo' in triggerconfig:
        repo_url = triggerconfig['repo']
        command = command.replace('REPODIR', os.path.join(repo_parent, get_repo_basename(repo_url)))
    command = command.replace('CACHEDIR', settings.REPOS_CACHE_DIR)
    if 'REPOVERSION' in command:
        version = get_repo_version(os.path.join(repo_parent, projectname))
        command = command.replace('REPOVERSION', version)

    for key in hook_info:
        if isinstance(hook_info[key], str):
            command = command.replace(key.upper(), hook_info[key])

    command = command.strip()  # ensure no weird linefeeds and superfluous whitespace are there
    logger.info('[%s] Executing `%s`', projectname, command)

    # TODO: capture_output is new in Python 3.7, replaces stdout and stderr
    # result = subprocess.run(command_parts, capture_output=True, check=True, shell=True, universal_newlines=True)
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True
    )
    return result


def do_pull_andor_command(config, hook_info):
    """Asynchronous task, performing the git pulling and the specified scripting inside a Process"""
    projectname = config[0]
    starttime = datetime.now()
    result = {'application': projectname}
    result['trigger'] = config[1]
    if 'repo' in config[1]:
        try:
            result['repo_result'] = update_repo(config)
            logger.info('[%s] result repo: %s', projectname, str(result['repo_result']))
        except git.GitCommandError as e:
            result = {'status': 'error', 'type': 'giterror', 'message': str(e)}
            logger.error('[%s] giterror: %s', projectname, str(e))
            result['runtime'] = datetime.now() - starttime
            notify_user(result, config)
            return
        except (OSError, KeyError) as e:
            result = {'status': 'error', 'type': 'oserror', 'message': str(e)}
            logger.error('[%s] oserror: %s', projectname, str(e))
            result['runtime'] = datetime.now() - starttime
            notify_user(result, config)
            return

    cmdresult = run_command(config, hook_info)
    if cmdresult and cmdresult.returncode == 0:
        logger.info('[%s] success for command: %s', projectname, str(cmdresult.stdout))
        result['status'] = 'OK'
    elif not cmdresult:
        logger.info('[%s] no command configured', projectname)
        result['status'] = 'OK'
    else:
        result['status'] = 'error'
        result['type'] = 'commanderror'
        result['message'] = cmdresult.stderr.strip()
        # TODO: seperate logfiles per job? Filename then based on appkey_triggerkey_timestamp.log
        logger.error(
            '[%s] commanderror with returncode %s: %s',
            projectname,
            str(cmdresult.returncode),
            cmdresult.stderr
        )
        logger.error('[%s] stdout: %s', projectname, cmdresult.stdout)
        logger.error('[%s] stderr: %s', projectname, cmdresult.stderr)

    result['runtime'] = datetime.now() - starttime

    if 'notify' not in config[1] or config[1]['notify'] != 'false':
        notify_user(result, config)


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
    logger.debug('Root page requested')
    return 'Welcome to <a href="https://github.com/aquatix/webhaak">Webhaak</a>, see the documentation to how to setup and use webhooks.'


@app.route('/admin/<secretkey>/list', methods=['GET'])
@crossdomain(origin='*')
def listtriggers(secretkey):
    """List the appkeys and triggerkeys"""
    try:
        if secretkey != settings.SECRETKEY:
            abort(404)
    except AttributeError:
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
    logger.info('%s on appkey: %s triggerkey: %s', request.method, appkey, triggerkey)
    config = get_trigger_settings(appkey, triggerkey)
    if config is None:
        logger.error('appkey/triggerkey combo not found')
        return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')

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
        else:
            vcs_source = '<unknown>'

        hook_info = {'vcs_source': vcs_source}
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
            event_info = 'received push from {} for '.format(vcs_source)
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
                # Only take info from the first change item
                hook_info['commit_before'] = payload['push']['changes'][0]['old']['target']['hash']
                hook_info['commit_after'] = payload['push']['changes'][0]['new']['target']['hash']
                hook_info['compare_url'] = payload['push']['changes'][0]['links']['html']['href']

                hook_info['commits'] = []
                for commit in payload['push']['changes'][0]['commits']:
                    commit_info = {'hash': commit['hash']}
                    commit_info['name'] = commit['author']['user']['username']
                    commit_info['email'] = commit['author']['raw']
                    hook_info['commits'].append(commit_info)

            if 'ref' in payload:
                hook_info['ref'] = payload['ref']
                if 'heads' in payload['ref']:
                    hook_info['branch'] = payload['ref'].replace('refs/heads/', '')
                elif 'tags' in payload['ref']:
                    hook_info['tag'] = payload['ref'].replace('refs/tags/', '')
            if 'repository' in payload:
                event_info += payload['repository']['full_name']
                hook_info['reponame'] = payload['repository']['full_name']
            if 'actor' in payload:
                # BitBucket pusher; no email address known here though
                event_info += ' by ' + payload['actor']['username']
                hook_info['username'] = payload['actor']['username']

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
        else:
            event_info += 'unknown, as no json was received. Check that {} webhook content type is application/json'.format(vcs_source)
        logger.debug(hook_info)
        logger.info(event_info)

    p = Process(target=do_pull_andor_command, args=(config, hook_info,))
    p.start()
    return Response(
        json.dumps({
            'status': 'OK',
            'message': 'Command accepted and will be run in the background'
        }), status=200, mimetype='application/json'
    )


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
