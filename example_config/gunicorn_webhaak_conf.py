# gunicorn_conf.py
from multiprocessing import cpu_count

bind = "127.0.0.1:8888"

# Worker Options
workers = cpu_count() + 1
worker_class = 'uvicorn.workers.UvicornWorker'

# Logging Options
loglevel = 'debug'
accesslog = '/var/log/webhaak/access_log'
errorlog = '/var/log/webhaak/error_log'
