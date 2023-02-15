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
# Log will rotate daily with a max history of LOG_BACKUP_COUNT
fh = logging.FileHandler(
    os.path.join(settings.LOG_DIR, 'webhaak.log')
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
                Optional("notify_on_error"): Bool(),
                Optional("repo"): Str(),
                Optional("repoparent"): Str(),
                Optional("branch"): Str(),
                Optional("command"): Str(),
                # Git author username -> friendly name mapping
                Optional("authors"): MapPattern(Str(), Str()),
                # Telegram
                Optional("telegram_chatid"): Str(),
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
    Optionally send a message to the configured Telegram chat.

    result is a dictionary with fields:
      command_result
      status: 'OK' | 'error'
      type: 'commanderror'
      message
    """
    try:
        triggerconfig = config[1]
        projectname = f'{config[0]}>{triggerconfig["title"]}'
        title = ''
        branch = triggerconfig.get('branch', 'master')
        command = triggerconfig.get('command', 'n/a')
        repo = triggerconfig.get('repo', 'n/a')
        message = f'repo: {repo}\nbranch: {branch}\ncommand: {command}\nruntime: {result.get("runtime")}'
        if result.get('status') == 'OK':
            title = f'Hook for {projectname} ran successfully'
        else:
            title = f'Hook for {projectname} failed: {result.get("type")}'
            message = f'{message}\n\n{result["message"]}'
        logging.debug(message)
        logging.info('Sending notification...')
        if triggerconfig.get('telegram_chatid') and triggerconfig.get('telegram_token'):
            telegram_chatid = triggerconfig['telegram_chatid']
            telegram_token = triggerconfig['telegram_token']
            # Send to Telegram chat
            msg = urllib.parse.quote_plus(make_sentry_message(result))
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage?chat_id={telegram_chatid}&text={msg}"
            )
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
            command = command.replace(key.upper(), hook_info[key].replace('"', '\\"'))

    command = command.strip()  # ensure no weird linefeeds and superfluous whitespace are there
    logger.info('[%s] Executing `%s`', projectname, command)

    # TODO: capture_output is new in Python 3.7, replaces stdout and stderr
    result = subprocess.run(command, capture_output=True, check=True, shell=True, universal_newlines=True)
    return result


def do_pull_andor_command(config, hook_info):
    """Asynchronous task, performing the git pulling and the specified scripting inside a Process"""
    this_job = get_current_job()

    projectname = config[0]
    starttime = datetime.now()
    result = {'application': projectname, 'result': 'unknown'}
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

    if 'command' in config[1]:
        cmdresult = run_command(config, hook_info)

        with open(f'{settings.LOG_DIR}/jobs/{this_job.id}.log', 'a', encoding='utf-8') as outfile:
            # Save output of the command ran by the job to its log
            outfile.write('== Command output ======\n')
            outfile.write(cmdresult)

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

    if (
        ('notify' not in config[1] or config[1]['notify'])
        or (result['status'] == 'error' and ('notify_on_error' in config[1] and config[1]['notify_on_error']))
    ):
        notify_user(result, config)
