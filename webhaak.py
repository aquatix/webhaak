from datetime import timedelta
from functools import update_wrapper
from flask import make_response, request, current_app
from flask import Flask
from flask import Response
from flask import jsonify
from werkzeug.exceptions import abort
import json
import logging
from logging.handlers import TimedRotatingFileHandler
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


class InvalidAPIUsage(Exception):
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

@app.errorhandler(InvalidAPIUsage)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    response.mimetype = 'application/json'
    return response


@app.route('/')
def indexpage():
    logger.debug('Root page requested')
    return 'Welcome to Webhaak, see the documentation to how to setup and use webhooks.'


@app.route('/app/<appkey>', methods=['GET', 'OPTIONS'])
@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
def approot(appkey):
    """
    List some generic info about the app
    """
    return appkey


@app.route('/app/<appkey>/<triggerkey>', methods=['GET', 'OPTIONS'])
@crossdomain(origin='*', max_age=settings.MAX_CACHE_AGE)
def apptrigger(appkey, triggerkey):
    """
    Fire the trigger described by the configuration under `triggerkey`
    """
    return appkey


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
#    import os
#    os.urandom(24)


@app.route('/getappkey')
def getappkey():
    """Generate new appkey"""
    import os
    return json.dumps({'key': os.urandom(24).encode('hex')})


if __name__ == '__main__':
    if settings.DEBUG == False:
        app.run(host='0.0.0.0', port=settings.PORT)
    else:
        app.run(port=settings.PORT)
