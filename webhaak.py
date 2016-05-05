import json
import logging
import git
import os
from subprocess import check_output
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


def update_repo(config):
    """
    Update (pull) the Git repo
    """
    projectname = config[0]
    triggerconfig = config[1]
    repo_url = triggerconfig['repo']
    logger.info('[' + projectname + '] Updating ' + repo_url)

    # Ensure cache dir for webhaak exists and is writable
    rw_dir = settings.REPOS_CACHE_DIR
    fileutil.ensure_dir_exists(rw_dir) # throws OSError if rw_dir is not writable

    repo_dir = os.path.join(rw_dir, projectname)
    if os.path.isdir(repo_dir):
        # Repo already exists locally, do a pull
        logger.info('[' + projectname + '] Repo exists, pull')

        apprepo = git.Repo(repo_dir)
        origin = apprepo.remote('origin')
        result = origin.fetch()                  # assure we actually have data. fetch() returns useful information
        origin.pull()
        result = str(result[0])
    else:
        # Repo needs to be cloned
        logger.info('[' + projectname + '] Repo does not exist yet, clone')
        empty_repo = git.Repo.init(repo_dir)
        origin = empty_repo.create_remote('origin', repo_url)
        origin.fetch()                  # assure we actually have data. fetch() returns useful information
        # Setup a local tracking branch of a remote branch
        empty_repo.create_head('master', origin.refs.master).set_tracking_branch(origin.refs.master)
        # push and pull behaves similarly to `git push|pull`
        result = origin.pull()
        result = str(result[0])
    return result


def run_command(config):
    """
    Run the command(s) defined for this trigger
    """
    projectname = config[0]
    triggerconfig = config[1]
    command = triggerconfig['command']
    logger.info('[' + projectname + '] Executing ' + command)
    # Replace some placeholders to be used in executing scripts from one of the repos
    command.replace('REPODIR', os.path.join(settings.REPOS_CACHE_DIR, projectname))
    command.replace('CACHEDIR', settings.REPOS_CACHE_DIR)

    command_parts = command.split(' ')
    result = check_output(command_parts)
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
    return 'Welcome to Webhaak, see the documentation to how to setup and use webhooks.'


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


@app.route('/app/<appkey>/<triggerkey>', methods=['GET', 'OPTIONS'])
@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
def apptrigger(appkey, triggerkey):
    """
    Fire the trigger described by the configuration under `triggerkey`
    """
    config = gettriggersettings(appkey, triggerkey)
    if config is None:
        #raise InvalidAPIUsage('Incorrect/incomplete parameter(s) provided', status_code=404)
        #raise NotFound('Incorrect app/trigger requested')
        abort(404)
    else:
        result = {}
        try:
            result['repo_result'] = update_repo(config)
        except git.GitCommandError as e:
            return Response(json.dumps({'type': 'giterror', 'message': str(e)}), status=500, mimetype='application/json')
        except OSError as e:
            return Response(json.dumps({'type': 'oserror', 'message': str(e)}), status=500, mimetype='application/json')

        try:
            result['command_result'] = run_command(config)
            return Response(json.dumps(result).replace('/', '\/'), status=200, mimetype='application/json')
        except OSError as e:
            return Response(json.dumps({'type': 'giterror', 'message': str(e)}), status=500, mimetype='application/json')

        return Response(json.dumps(result), status=200, mimetype='application/json')


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
        app.run(host='0.0.0.0', port=settings.PORT)
    else:
        app.run(port=settings.PORT)
