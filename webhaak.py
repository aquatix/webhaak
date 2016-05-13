import json
import logging
import git
import os
from subprocess import check_output, STDOUT, CalledProcessError
from logging.handlers import TimedRotatingFileHandler
from datetime import timedelta
from functools import update_wrapper
from flask import make_response, request, current_app
from flask import Flask
from flask import Response
from flask import jsonify
from werkzeug.exceptions import abort
from utilkit import fileutil
import settings

app = Flask(__name__)
app.debug = settings.DEBUG

logger = logging.getLogger('webhaak')
logger.setLevel(logging.DEBUG)
#fh = logging.handlers.RotatingFileHandler('dcp_search.log', maxBytes=100000000, backupCount=10)
# Log will rotate daily with a max history of LOG_BACKUP_COUNT
fh = TimedRotatingFileHandler(settings.LOG_LOCATION, when='d', interval=1, backupCount=settings.LOG_BACKUP_COUNT)
fh.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

with open(settings.PROJECTS_FILE, 'r') as pf:
    projects = fileutil.yaml_ordered_load(pf, fileutil.yaml.SafeLoader)


def gettriggersettings(appkey, triggerkey):
    """
    Look up the trigger and return the repo and command to be updated and fired
    """
    for project in projects:
        if projects[project]['appkey'] == appkey:
            for trigger in projects[project]['triggers']:
                if projects[project]['triggers'][trigger]['triggerkey'] == triggerkey:
                    return (project, projects[project]['triggers'][trigger])
    return None


def get_repo_basename(repo_url):
    """
    Extract repository basename from its url, as that will be the name of  directory it will be cloned into1
    """
    result = os.path.basename(repo_url)
    filename, file_extension = os.path.splitext(result)
    if file_extension == '.git':
        # Strip the .git from the name, as Git will do the same on non-bare checkouts
        result = filename
    return result


def update_repo(config):
    """
    Update (pull) the Git repo
    """
    projectname = config[0]
    triggerconfig = config[1]

    repo_url = triggerconfig['repo']
    repo_parent = settings.REPOS_CACHE_DIR
    if 'repoparent' in triggerconfig and triggerconfig['repoparent']:
        repo_parent = triggerconfig['repoparent']

    logger.info('[' + projectname + '] Updating ' + repo_url)
    logger.info('[' + projectname + '] Repo parent ' + repo_parent)

    # Ensure cache dir for webhaak exists and is writable
    fileutil.ensure_dir_exists(repo_parent) # throws OSError if repo_parent is not writable

    # TODO: check whether dir exists with different repository
    repo_dir = os.path.join(repo_parent, get_repo_basename(repo_url))
    logger.info('[' + projectname + '] Repo dir ' + repo_dir)
    if os.path.isdir(repo_dir):
        # Repo already exists locally, do a pull
        logger.info('[' + projectname + '] Repo exists, pull')

        apprepo = git.Repo(repo_dir)
        origin = apprepo.remote('origin')
        result = origin.fetch()                  # assure we actually have data. fetch() returns useful information
        origin.pull()
        logger.info('[' + projectname + '] Done pulling, checkout()')
        #logger.debug(apprepo.git.branch())
        result = apprepo.git.checkout()
    else:
        # Repo needs to be cloned
        logger.info('[' + projectname + '] Repo does not exist yet, clone')
        apprepo = git.Repo.init(repo_dir)
        origin = apprepo.create_remote('origin', repo_url)
        origin.fetch()                  # assure we actually have data. fetch() returns useful information
        # Setup a local tracking branch of a remote branch
        apprepo.create_head('master', origin.refs.master).set_tracking_branch(origin.refs.master)
        # push and pull behaves similarly to `git push|pull`
        result = origin.pull()
        logger.info('[' + projectname + '] Done pulling, checkout()')
        #logger.debug(apprepo.git.branch())
        result = apprepo.git.checkout()
    return result


def run_command(config):
    """
    Run the command(s) defined for this trigger
    """
    projectname = config[0]
    triggerconfig = config[1]
    if 'command' not in triggerconfig:
        # No command to execute, return
        logger.info('[' + projectname + '] No command to execute')
        return None
    command = triggerconfig['command']
    # Replace some placeholders to be used in executing scripts from one of the repos
    command = command.replace('REPODIR', os.path.join(settings.REPOS_CACHE_DIR, projectname))
    command = command.replace('CACHEDIR', settings.REPOS_CACHE_DIR)
    logger.info('[' + projectname + '] Executing ' + command)

    command_parts = command.split(' ')
    logger.info(str(command_parts))
    command_parameters = ' '.join(command_parts[1:])
    result = check_output(command_parameters, executable=command_parts[0], stderr=STDOUT, shell=True)
    return result


# == API request support functions/mixins ======

def crossdomain(origin=None, methods=None, headers=None,
                max_age=21600, attach_to_all=True,
                automatic_options=True):
    """
    Decorator to send the correct cross-domain headers
    src: https://blog.skyred.fi/articles/better-crossdomain-snippet-for-flask.html
    """
    if methods is not None:
        methods = ', '.join(sorted(x.upper() for x in methods))
    if headers is not None and not isinstance(headers, basestring):
        headers = ', '.join(x.upper() for x in headers)
    if not isinstance(origin, basestring):
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
@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
def apptrigger(appkey, triggerkey):
    """
    Fire the trigger described by the configuration under `triggerkey`
    """
    logger.info(request.method + ' on appkey: ' + appkey + ' triggerkey: ' + triggerkey)
    if request.method == 'POST':
        # Likely some ping was sent, check if so
        if request.headers.get('X-GitHub-Event') == "ping":
            payload = request.get_json()
            logger.info('received GitHub ping for ' + payload['repository']['full_name'] + ' hook: ' + payload['hook']['url'])
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
        abort(404)
    else:
        result = {'application': config[0]}
        try:
            result['repo_result'] = update_repo(config)
            logger.info('result repo: ' + str(result['repo_result']))
        except git.GitCommandError as e:
            result = {'status': 'error', 'type': 'giterror', 'message': str(e)}
            logger.error('giterror: ' + str(e))
            return Response(json.dumps(result).replace('/', '\/'), status=412, mimetype='application/json')
        except OSError as e:
            result = {'status': 'error', 'type': 'oserror', 'message': str(e)}
            logger.error('oserror: ' + str(e))
            return Response(json.dumps(result).replace('/', '\/'), status=412, mimetype='application/json')

        try:
            result['command_result'] = run_command(config)
            logger.info('result command: ' + str(result['command_result']))
        except (OSError, CalledProcessError) as e:
            result['status'] = 'error'
            result['type'] = 'commanderror'
            result['message'] = str(e)
            logger.error('commanderror: ' + str(e))
            return Response(json.dumps(result), status=412, mimetype='application/json')

        result['status'] = 'OK'
        return Response(json.dumps(result).replace('/', '\/'), status=200, mimetype='application/json')


@app.route('/monitor')
@app.route('/monitor/')
@app.route('/monitor/monitor.html')
def monitor():
    """
    Monitoring ping
    """
    result = 'OK'
    return result



# New in Flask 1.0, not released yet
#@app.cli.command()
#def getappkey():
#    """Generate new appkey"""
#    os.urandom(24)


@app.route('/getappkey')
def getappkey():
    """Generate new appkey"""
    return json.dumps({'key': os.urandom(24).encode('hex')})


if __name__ == '__main__':
    if settings.DEBUG == False:
        app.run(host='0.0.0.0', port=settings.PORT, debug=settings.DEBUG)
    else:
        app.run(port=settings.PORT, debug=settings.DEBUG)
