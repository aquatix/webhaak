import logging
import os
import subprocess
import time
from datetime import datetime

import git
import requests
import strictyaml
from pydantic import DirectoryPath, FilePath, validator
from pydantic_settings import BaseSettings
from rq import get_current_job
from strictyaml import Bool, Map, MapPattern, Optional, Seq, Str


class Settings(BaseSettings):
    """
    Configuration needed for webhaak to do its tasks, using environment variables
    """
    secretkey: str
    log_dir: DirectoryPath
    jobs_log_dir: DirectoryPath = 'jobs'
    eventlog_dir: DirectoryPath
    projects_file: FilePath

    repos_cache_dir: DirectoryPath

    pushover_userkey: str
    pushover_apptoken: str

    debug: bool = False

    @validator('jobs_log_dir', pre=True)
    def apply_root(cls, value, values):
        """
        Create the actual value for jobs_log_dir, through its validator
        """
        if log_dir := values.get('log_dir'):
            # jobs_log_dir is a subdirectory of log_dir
            return log_dir / value
        # should only happen when there was an error with log_dir
        return value


# Read the settings from the environment, based on the above configuration
settings = Settings()
print(settings.dict())

logger = logging.getLogger('worker')

logger.setLevel(logging.DEBUG)
fh = logging.FileHandler(
    os.path.join(settings.log_dir, 'webhaak.log')
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
            # Key for a group of triggers
            "app_key": Str(),
            "triggers": MapPattern(Str(), Map({
                # Key for a specific trigger
                "trigger_key": Str(),
                Optional("notify"): Bool(),
                Optional("notify_on_error"): Bool(),
                # Git repository URI
                Optional("repo"): Str(),
                # Parent directory for repository to clone to; defaults to REPOS_CACHE_DIR
                Optional("repo_parent"): Str(),
                # Only act when incoming call is about this specific branch; if unspecified, trigger will always fire
                Optional("branch"): Str(),
                # Execute this command (after Git pull if repo specified)
                Optional("command"): Str(),
                # Git author username -> friendly name mapping
                Optional("authors"): MapPattern(Str(), Str()),
                # Telegram
                Optional("telegram_chat_id"): Str(),
                Optional("telegram_token"): Str(),
                # Sentry
                Optional("ignore"): Seq(Str()),
            }))
        }
    )
)

# Load the configuration of the various projects/hooks
with open(settings.projects_file, 'r', encoding='utf-8') as pf:
    projects = strictyaml.load(pf.read(), schema).data


def send_pushover_message(userkey, apptoken, text, **kwargs):
    """
    Send a message through PushOver

    It is possible to specify additional properties of the message by passing keyword
    arguments. The list of valid keywords is ``title, priority, sound,
    callback, timestamp, url, url_title, device, retry, expire and html``
    which are described in the Pushover API documentation.

    For convenience, you can simply set ``timestamp=True`` to set the
    timestamp to the current timestamp.

    :param str userkey: user key in PushOver
    :param str apptoken: app token for PushOver
    :param str text: message to send
    """
    message_keywords = [
        "title",
        "priority",
        "sound",
        "callback",
        "timestamp",
        "url",
        "url_title",
        "device",
        "retry",
        "expire",
        "html",
        "attachment",
    ]
    payload = {"message": text, "user": userkey, "token": apptoken}
    for key, value in kwargs.items():
        if key not in message_keywords:
            raise ValueError(f'{key}: invalid message parameter')
        if key == "timestamp" and value is True:
            payload[key] = int(time.time())
        else:
            payload[key] = value
    response = requests.post(
        'https://api.pushover.net/1/messages.json',
        data=payload,
        headers={'User-Agent': 'Python'},
        timeout=60
    )
    return response


def make_sentry_message(result):
    """
    # Filter away known things
    if [[ $MESSAGE == *"Het ElementTree object kon niet"* ||
          $MESSAGE == *"The ElementTree object could"* ||
          $MESSAGE == *"Meerdere resultaten gevonden in de wachtrij"* ||
          $MESSAGE == *"Found multiple results in"* ||
          $MESSAGE == *"Openen video is mislukt voor"* ||
          $MESSAGE == *"SAML login mislukt voor organisatie"* ||
          $MESSAGE == *"Cannot read property 'mData' of undefined"* ||
          $MESSAGE == *"Cannot find tmlo for id"* ]];
    then
        exit
    fi

    URL=${URL//?referrer=webhooks_plugin/}

    # Include stacktrace when available
    if [ "$STACKTRACE" != "Not available" ]; then
        TRACETEXT="
    ${STACKTRACE}

    "

    # Replace literal \n with end of lines
    TRACETEXT=${TRACETEXT//\\n/
    }
    fi

    # Create the message to send
    REPORT="[${PROJECTNAME}] ${MESSAGE}

    in *${CULPRIT}*
    ${TRACETEXT}
    ${URL}"
    """
    return ''


def notify_user(result, config):
    """Send a PushOver message if configured, after git operation and command have run.
    Optionally send a message to the configured Telegram chat instead.

    result is a dictionary with fields:
      command_result
      status: 'OK' | 'error'
      type: 'command_error'
      message
    """
    try:
        trigger_config = config[1]
        projectname = f'{config[0]}>{trigger_config["title"]}'
        title = ''
        branch = trigger_config.get('branch', 'master')
        command = trigger_config.get('command', 'n/a')
        repo = trigger_config.get('repo', 'n/a')
        message = f'repo: {repo}\nbranch: {branch}\ncommand: {command}\nruntime: {result.get("runtime")}'
        if result.get('status') == 'OK':
            title = f'Hook for {projectname} ran successfully'
        else:
            title = f'Hook for {projectname} failed: {result.get("type")}'
            message = f'{message}\n\n{result.get("message")}'
        logging.debug(message)
        logging.info('Sending notification...')
        if trigger_config.get('telegram_chat_id') and trigger_config.get('telegram_token'):
            telegram_chat_id = trigger_config['telegram_chat_id']
            telegram_token = trigger_config['telegram_token']
            # Send to Telegram chat
            msg = requests.utils.quote(make_sentry_message(result), safe='')
            with requests.get(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage?chat_id={telegram_chat_id}&text={msg}",
                timeout=30
            ) as response:
                logging.info('Telegram notification sent, result was %s', str(response.status_code))
        else:
            # Use the Pushover default
            response = send_pushover_message(
                settings.pushover_userkey,
                settings.pushover_apptoken,
                message,
                title=title
            )
            if not response.status_code == 200:
                logging.error(response.text)
        logging.info('Notification sent')
    except AttributeError:
        logging.warning('Notification through PushOver failed because of missing configuration')


def get_trigger_settings(app_key, trigger_key):
    """Look up the trigger and return the repo and command to be updated and fired

    :param app_key: application key part of the url
    :param trigger_key: trigger key part of the url, sub part of the config
    :return: tuple with project info and the trigger config
    """
    for project in projects:
        if projects[project]['app_key'] == app_key:
            for trigger in projects[project]['triggers']:
                if projects[project]['triggers'][trigger]['trigger_key'] == trigger_key:
                    trigger_config = projects[project]['triggers'][trigger]
                    trigger_config['title'] = trigger
                    return project, trigger_config
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


def fetch_info_to_str(fetch_info):
    """git.remote.FetchInfo to human-readable representation"""
    result = fetch_info[0].note
    return result


def update_repo(config):
    """Update (pull) the Git repo"""
    projectname = config[0]
    trigger_config = config[1]

    repo_url = trigger_config['repo']
    repo_parent = settings.repos_cache_dir
    if 'repo_parent' in trigger_config and trigger_config['repo_parent']:
        repo_parent = trigger_config['repo_parent']

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

        app_repo = git.Repo(repo_dir)
        origin = app_repo.remote('origin')
        result = fetch_info_to_str(origin.fetch())  # assure we actually have data. fetch() returns useful information
        logger.info('[%s] Fetch result: %s', projectname, result)
    else:
        # Repo needs to be cloned
        logger.info('[%s] Repo does not exist yet, clone', projectname)
        app_repo = git.Repo.init(repo_dir)
        origin = app_repo.create_remote('origin', repo_url)
        origin.fetch()                  # assure we actually have data. fetch() returns useful information
        # Set up a local tracking branch of a remote branch
        app_repo.create_head('master', origin.refs.master).set_tracking_branch(origin.refs.master)
    branch = 'master'
    if 'branch' in trigger_config:
        branch = trigger_config['branch']
    logger.info('[%s] checkout() branch \'%s\'', projectname, branch)
    result = str(app_repo.git.checkout(branch))
    # pull (so really update) the checked out branch to latest commit
    origin.pull()
    logger.info('[%s] Done pulling branch \'%s\'', projectname, branch)
    return result


def run_command(config, hook_info):
    """Run the command(s) defined for this trigger"""
    projectname = config[0]
    trigger_config = config[1]
    if 'command' not in trigger_config:
        # No command to execute, return
        logger.info('[%s] No command to execute', projectname)
        return None
    command = trigger_config['command']
    # Replace some placeholders to be used in executing scripts from one of the repos
    repo_parent = settings.repos_cache_dir
    if 'repo_parent' in trigger_config and trigger_config['repo_parent']:
        repo_parent = trigger_config['repo_parent']
    if 'repo' in trigger_config:
        repo_url = trigger_config['repo']
        command = command.replace('REPODIR', os.path.join(repo_parent, get_repo_basename(repo_url)))
    command = command.replace('CACHEDIR', str(settings.repos_cache_dir))
    if 'REPOVERSION' in command:
        version = get_repo_version(os.path.join(repo_parent, projectname))
        command = command.replace('REPOVERSION', version)

    for key in hook_info:
        if isinstance(hook_info[key], str):
            command = command.replace(key.upper(), hook_info[key].replace('"', '\\"'))

    command = command.strip()  # ensure no weird line feeds and superfluous whitespace are there
    logger.info('[%s] Executing `%s`', projectname, command)

    result = subprocess.run(command, capture_output=True, check=True, shell=True, universal_newlines=True)
    return result


def do_pull_andor_command(config, hook_info):
    """Asynchronous task, performing the git pulling and the specified scripting inside a Process"""
    this_job = get_current_job()

    projectname = config[0]
    start_time = datetime.now()
    result = {'application': projectname, 'result': 'unknown', 'trigger': config[1]}
    if 'repo' in config[1]:
        try:
            result['repo_result'] = update_repo(config)
            logger.info('[%s] result repo: %s', projectname, str(result['repo_result']))
            result['status'] = 'OK'
        except git.GitCommandError as e:
            result = {'status': 'error', 'type': 'git_error', 'message': str(e)}
            logger.error('[%s] git_error: %s', projectname, str(e))
            result['runtime'] = datetime.now() - start_time
            notify_user(result, config)
            return
        except (OSError, KeyError) as e:
            result = {'status': 'error', 'type': 'os_error', 'message': str(e)}
            logger.error('[%s] os_error: %s', projectname, str(e))
            result['runtime'] = datetime.now() - start_time
            notify_user(result, config)
            return

    if 'command' in config[1]:
        cmd_result = None
        cmd_error = None
        try:
            cmd_result = run_command(config, hook_info)
        except subprocess.CalledProcessError as e:
            logger.error('[%s] Error while executing command: %s', projectname, str(e))
            cmd_error = str(e)

        with open(os.path.join(settings.jobs_log_dir, f'{this_job.id}.log'), 'a', encoding='utf-8') as outfile:
            # Save output of the command ran by the job to its log
            if cmd_result:
                outfile.write(f'== Command returncode: {cmd_result.returncode} ======\n')
                outfile.write('== Command output ======\n')
                outfile.write(cmd_result.stdout)
                outfile.write('== Command error, if any ======\n')
                outfile.write(cmd_result.stderr)
            else:
                outfile.write('== Command error ======\n')
                outfile.write(cmd_error)

        if cmd_result and cmd_result.returncode == 0:
            logger.info('[%s] success for command: %s', projectname, str(cmd_result.stdout))
            result['status'] = 'OK'
        elif not cmd_result and not cmd_error:
            logger.info('[%s] no command configured', projectname)
            result['status'] = 'OK'
        else:
            result['status'] = 'error'
            result['type'] = 'command_error'
            if cmd_result:
                result['message'] = cmd_result.stderr.strip()
                logger.error(
                    '[%s] command_error with returncode %s: %s',
                    projectname,
                    str(cmd_result.returncode),
                    cmd_result.stderr
                )
                logger.error('[%s] stdout: %s', projectname, cmd_result.stdout)
                logger.error('[%s] stderr: %s', projectname, cmd_result.stderr)
            elif cmd_error:
                result['message'] = cmd_error.strip()
            else:
                result['message'] = 'Unknown error'

    result['runtime'] = datetime.now() - start_time

    if (
        ('notify' not in config[1] or config[1]['notify'])
        or (result['status'] == 'error' and ('notify_on_error' in config[1] and config[1]['notify_on_error']))
    ):
        notify_user(result, config)
