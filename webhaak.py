import binascii
import json
import logging
import os
from datetime import timedelta
from functools import update_wrapper
from logging.handlers import TimedRotatingFileHandler
from multiprocessing import Process
import subprocess

import click
import git
import pushover
import yaml
from flask import Flask, Response, current_app, jsonify, make_response, request
from utilkit import fileutil

import settings

app = Flask(__name__)
app.debug = settings.DEBUG

logger = logging.getLogger('webhaak')
logger.setLevel(logging.DEBUG)
#fh = logging.handlers.RotatingFileHandler('dcp_search.log', maxBytes=100000000, backupCount=10)
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

# Load the configuration of the various projects/hooks
with open(settings.PROJECTS_FILE, 'r') as pf:
    projects = fileutil.yaml_ordered_load(pf, yaml.SafeLoader)


def notify_user(result, config):
    """Send a PushOver message if configured, after git and command have run

    result is a dictionary with fields:
      command_result
      status: 'OK' | 'error'
      type: 'commanderror'
      message
    """
    try:
        projectname = config[0]
        triggerconfig = config[1]
        title = ''
        branch = 'master'
        if 'repo_branch' in triggerconfig:
            branch = triggerconfig['repo_branch']
        message = 'repo: {}\nbranch: {}\ncommand: {}'.format(triggerconfig['repo'], branch, triggerconfig['command'])
        if result['status'] == 'OK':
            title = "Hook for {} ran successfully".format(projectname)
        else:
            title = "Hook for {} failed: {}".format(projectname, result['type'])
            message = message + '\n\n{}'.format(result['message'])
        logging.debug(message)
        logging.info('Sending notification...')
        client = pushover.Client(settings.PUSHOVER_USERKEY, api_token=settings.PUSHOVER_APPTOKEN)
        client.send_message(message, title=title)
        logging.info('Notification sent')
    except AttributeError:
        logging.warning('Notification through PushOver failed because of missing configuration')


def gettriggersettings(appkey, triggerkey):
    """Look up the trigger and return the repo and command to be updated and fired"""
    for project in projects:
        if projects[project]['appkey'] == appkey:
            for trigger in projects[project]['triggers']:
                if projects[project]['triggers'][trigger]['triggerkey'] == triggerkey:
                    return (project, projects[project]['triggers'][trigger])
    return None


def get_repo_basename(repo_url):
    """Extract repository basename from its url, as that will be the name of directory it will be cloned into"""
    result = os.path.basename(repo_url)
    filename, file_extension = os.path.splitext(result)
    if file_extension == '.git':
        # Strip the .git from the name, as Git will do the same on non-bare checkouts
        result = filename
    return result


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
    fileutil.ensure_dir_exists(repo_parent) # throws OSError if repo_parent is not writable

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
        origin.pull()
        logger.info('[%s] Done pulling, checkout()', projectname)
        #logger.debug(apprepo.git.branch())
        result += ' ' + str(apprepo.git.checkout())
    else:
        # Repo needs to be cloned
        logger.info('[%s] Repo does not exist yet, clone', projectname)
        apprepo = git.Repo.init(repo_dir)
        origin = apprepo.create_remote('origin', repo_url)
        origin.fetch()                  # assure we actually have data. fetch() returns useful information
        # Setup a local tracking branch of a remote branch
        apprepo.create_head('master', origin.refs.master).set_tracking_branch(origin.refs.master)
        # push and pull behaves similarly to `git push|pull`
        result = origin.pull()
        logger.info('[%s] Done pulling, checkout()', projectname)
        #logger.debug(apprepo.git.branch())
        result += ' ' + str(apprepo.git.checkout())
    return result


def run_command(config):
    """Run the command(s) defined for this trigger"""
    projectname = config[0]
    triggerconfig = config[1]
    if 'command' not in triggerconfig:
        # No command to execute, return
        logger.info('[%s] No command to execute', projectname)
        return None
    command = triggerconfig['command']
    # Replace some placeholders to be used in executing scripts from one of the repos
    command = command.replace('REPODIR', os.path.join(settings.REPOS_CACHE_DIR, projectname))
    command = command.replace('CACHEDIR', settings.REPOS_CACHE_DIR)
    command = command.strip()  # ensure no weird linefeeds and superfluous whitespace are there
    logger.info('[%s] Executing `%s`', projectname, command)

    # TODO: capture_output is new in Python 3.7, replaces stdout and stderr
    #result = subprocess.run(command_parts, capture_output=True, check=True, shell=True, universal_newlines=True)
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True
    )
    return result


def do_pull_andor_command(config):
    """Asynchronous task, performing the git pulling and the specified scripting inside a Process"""
    projectname = config[0]
    result = {'application': projectname}
    result['trigger'] = config[1]
    if 'repo' in config[1]:
        try:
            result['repo_result'] = update_repo(config)
            logger.info('[%s] result repo: %s', projectname, str(result['repo_result']))
        except git.GitCommandError as e:
            result = {'status': 'error', 'type': 'giterror', 'message': str(e)}
            logger.error('[%s] giterror: %s', projectname, str(e))
            notify_user(result, config)
        except (OSError, KeyError) as e:
            result = {'status': 'error', 'type': 'oserror', 'message': str(e)}
            logger.error('[%s] oserror: %s', projectname, str(e))
            notify_user(result, config)

    cmdresult = run_command(config)
    if cmdresult.returncode == 0:
        logger.info('[%s] success for command: %s', projectname, str(cmdresult.stdout))
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


#@app.route('/app/<appkey>', methods=['GET', 'OPTIONS'])
#@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
#def approot(appkey):
#    """
#    List some generic info about the app
#    """
#    for app in projects:
#        print app
#        if projects[app]['appkey'] == appkey:
#            print 'found'
#            return app
#    return appkey


@app.route('/app/<appkey>/<triggerkey>', methods=['GET', 'OPTIONS', 'POST'])
#@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
def apptrigger(appkey, triggerkey):
    """Fire the trigger described by the configuration under `triggerkey`"""
    logger.info('%s on appkey: %s triggerkey: %s', request.method, appkey, triggerkey)
    if request.method == 'POST':
        # Likely some ping was sent, check if so
        if request.headers.get('X-GitHub-Event') == "ping":
            payload = request.get_json()
            logger.info('received GitHub ping for %s hook: %s ', payload['repository']['full_name'], payload['hook']['url'])
            return json.dumps({'msg': 'Hi!'})
        if request.headers.get('X-GitHub-Event') != "push":
            payload = request.get_json()
            logger.info('received wrong event type from GitHub for ' + payload['repository']['full_name'] + ' hook: ' + payload['hook']['url'])
            return json.dumps({'msg': "wrong event type"})
        else:
            payload = request.get_json()
            event_info = 'received push from GitHub for '
            if 'repository' in payload:
                event_info += payload['repository']['full_name']
            if 'pusher' in payload:
                event_info += ' by ' + payload['pusher']['name']
            if 'compare' in payload:
                event_info += ', compare: ' + payload['compare']
            logger.info(payload)
            logger.info(event_info)

    config = gettriggersettings(appkey, triggerkey)
    if config is None:
        #raise InvalidAPIUsage('Incorrect/incomplete parameter(s) provided', status_code=404)
        #raise NotFound('Incorrect app/trigger requested')
        logger.error('appkey/triggerkey combo not found')
        return Response(json.dumps({'status': 'Error'}), status=404, mimetype='application/json')
    p = Process(target=do_pull_andor_command, args=(config,))
    p.start()
    return Response(
        json.dumps({
            'status': 'OK',
            'message': 'Command accepted and will be run in the background'
        }), status=200, mimetype='application/json'
    )


@app.route('/monitor')
#@app.route('/monitor/')
#@app.route('/monitor/monitor.html')
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
    return Response(json.dumps({'key': generatekey().decode('utf-8')}, status=200, mimetype='application/json'))


if __name__ == '__main__':
    if settings.DEBUG == False:
        app.run(port=settings.PORT, debug=settings.DEBUG)
    else:
        app.run(host='0.0.0.0', port=settings.PORT, debug=settings.DEBUG)
