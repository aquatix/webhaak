import logging
import os
import subprocess
import urllib
from datetime import datetime

import git
import pushover
from rq import get_current_job
import strictyaml
from strictyaml import Bool, Map, MapPattern, Optional, Seq, Str

import settings

logger = logging.getLogger('worker')

logger.setLevel(logging.DEBUG)
LOG_DIR = os.getenv('LOG_DIR', os.getcwd())
JOBSLOG_DIR = os.path.join(LOG_DIR, 'jobs')
fh = logging.FileHandler(
    os.path.join(LOG_DIR, 'webhaak.log')
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
            "app_key": Str(),
            "triggers": MapPattern(Str(), Map({
                "trigger_key": Str(),
                Optional("notify"): Bool(),
                Optional("notify_on_error"): Bool(),
                Optional("repo"): Str(),
                Optional("repo_parent"): Str(),
                Optional("branch"): Str(),
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
PROJECTS_FILE = os.getenv("PROJECTS_FILE", "projects.yaml")
print(f"PROJECTS_FILE: {PROJECTS_FILE}")
with open(PROJECTS_FILE, 'r', encoding='utf-8') as pf:
    projects = strictyaml.load(pf.read(), schema).data

print(projects)


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
      type: 'commanderror'
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
            msg = urllib.parse.quote_plus(make_sentry_message(result))
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage?chat_id={telegram_chat_id}&text={msg}"
            ) as response:
                logging.info('Telegram notification sent, result was %s', str(response.status))
        else:
            # Use the Pushover default
            client = pushover.Pushover(settings.PUSHOVER_APPTOKEN)
            client.message(settings.PUSHOVER_USERKEY, message, title=title)
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
    """git.remote.FetchInfo to human-readable representation"""
    result = fetchinfo[0].note
    return result


def update_repo(config):
    """Update (pull) the Git repo"""
    projectname = config[0]
    trigger_config = config[1]

    repo_url = trigger_config['repo']
    repo_parent = settings.REPOS_CACHE_DIR
    if 'repoparent' in trigger_config and trigger_config['repoparent']:
        repo_parent = trigger_config['repoparent']

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
        result = fetchinfo_to_str(origin.fetch())  # assure we actually have data. fetch() returns useful information
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
    repo_parent = settings.REPOS_CACHE_DIR
    if 'repoparent' in trigger_config and trigger_config['repoparent']:
        repo_parent = trigger_config['repoparent']
    if 'repo' in trigger_config:
        repo_url = trigger_config['repo']
        command = command.replace('REPODIR', os.path.join(repo_parent, get_repo_basename(repo_url)))
    command = command.replace('CACHEDIR', settings.REPOS_CACHE_DIR)
    if 'REPOVERSION' in command:
        version = get_repo_version(os.path.join(repo_parent, projectname))
        command = command.replace('REPOVERSION', version)

    for key in hook_info:
        if isinstance(hook_info[key], str):
            command = command.replace(key.upper(), hook_info[key].replace('"', '\\"'))

    command = command.strip()  # ensure no weird linefeeds and superfluous whitespace are there
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
            result = {'status': 'error', 'type': 'giterror', 'message': str(e)}
            logger.error('[%s] giterror: %s', projectname, str(e))
            result['runtime'] = datetime.now() - start_time
            notify_user(result, config)
            return
        except (OSError, KeyError) as e:
            result = {'status': 'error', 'type': 'oserror', 'message': str(e)}
            logger.error('[%s] oserror: %s', projectname, str(e))
            result['runtime'] = datetime.now() - start_time
            notify_user(result, config)
            return

    if 'command' in config[1]:
        cmd_result = run_command(config, hook_info)

        with open(os.path.join(JOBSLOG_DIR, f'{this_job.id}.log'), 'a', encoding='utf-8') as outfile:
            # Save output of the command ran by the job to its log
            outfile.write(f'== Command returncode: {cmd_result.returncode} ======\n')
            outfile.write('== Command output ======\n')
            outfile.write(cmd_result.stdout)
            outfile.write('== Command error, if any ======\n')
            outfile.write(cmd_result.stderr)

        if cmd_result and cmd_result.returncode == 0:
            logger.info('[%s] success for command: %s', projectname, str(cmd_result.stdout))
            result['status'] = 'OK'
        elif not cmd_result:
            logger.info('[%s] no command configured', projectname)
            result['status'] = 'OK'
        else:
            result['status'] = 'error'
            result['type'] = 'commanderror'
            result['message'] = cmd_result.stderr.strip()
            logger.error(
                '[%s] commanderror with returncode %s: %s',
                projectname,
                str(cmd_result.returncode),
                cmd_result.stderr
            )
            logger.error('[%s] stdout: %s', projectname, cmd_result.stdout)
            logger.error('[%s] stderr: %s', projectname, cmd_result.stderr)

    result['runtime'] = datetime.now() - start_time

    if (
        ('notify' not in config[1] or config[1]['notify'])
        or (result['status'] == 'error' and ('notify_on_error' in config[1] and config[1]['notify_on_error']))
    ):
        notify_user(result, config)
