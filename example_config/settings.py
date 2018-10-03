# Virtualenv to use with the wsgi file
VENV = '/srv/hook.example.com/venv/bin/activate_this.py'

PORT = 8086

MAX_CACHE_AGE = 600  # seconds

DEBUG = False

PROJECTS_FILE = 'projects.yaml'
#PROJECTS_FILE = '/srv/hook.example.com/config/projects.yaml'
REPOS_CACHE_DIR = '/var/cache/webhaak'

# SECRETKEY is used for listing projects and other admin urls
# SECRETKEY = 'myNiceLittleSecret'

LOG_LOCATION = 'webhaak.log'
#LOG_LOCATION = '/var/log/webhaak/webhaak.log'
# How many logs to keep in log rotation:
LOG_BACKUP_COUNT = 10

#PUSHOVER_USERKEY = '<fill_in_your_userkey>'
#PUSHOVER_APPTOKEN = '<fill_in_the_apptoken_you_created>'
