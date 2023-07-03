import logging

logger = logging.getLogger('webhaak')


def handle_bitbucket_push(payload, hook_info):
    """Handle an incoming Git push hook from BitBucket

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    """
    logger.debug('Amount of changes in this push: %d', len(payload['push']['changes']))
    hook_info['commit_before'] = None  # When a branch is created, old is null; use as default
    # Only take info from the first change item
    if payload['push']['changes'][0]['old']:
        # Info on the previous commit is available (so not a new branch)
        hook_info['commit_before'] = payload['push']['changes'][0]['old']['target']['hash']
    if payload['push']['changes'][0]['new']:
        # Info about the (merge) commit is known
        hook_info['commit_after'] = payload['push']['changes'][0]['new']['target']['hash']
    else:
        # Likely a 'None' merge commit, so get the info from the branch that is getting merged
        hook_info['commit_after'] = payload['push']['changes'][0]['old']['target']['hash']

    if 'links' in payload['push']['changes'][0] and 'html' in payload['push']['changes'][0]['links']:
        hook_info['compare_url'] = payload['push']['changes'][0]['links']['html']['href']
    elif (
        payload['push']['changes'][0]['old']
        and 'links' in payload['push']['changes'][0]['old']
        and 'html' in payload['push']['changes'][0]['old']['links']
    ):
        hook_info['compare_url'] = payload['push']['changes'][0]['old']['links']['html']['href']
    else:
        hook_info['compare_url'] = ''

    # Whether branch was closed; most likely after a merge
    hook_info['closed'] = payload['push']['changes'][0].get('closed', False)
    hook_info['commits'] = []
    commits = payload['push']['changes'][0].get('commits', [])
    for commit in commits:
        commit_info = {'hash': commit['hash']}
        if 'user' in commit['author']:
            if 'username' in commit['author']['user']:
                commit_info['name'] = commit['author']['user']['username']
            else:
                commit_info['name'] = commit['author']['user']['nickname']
        commit_info['email'] = commit['author']['raw']
        hook_info['commits'].append(commit_info)


def handle_bitbucket_pullrequest(payload, hook_info):
    """Handle an incoming pull request hook from BitBucket

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    """
    if 'rendered' in payload['pullrequest']:
        hook_info['pullrequest_title'] = payload['pullrequest']['rendered']['title']['raw']
        hook_info['pullrequest_description'] = payload['pullrequest']['rendered']['description']['raw']
    if 'close_source_branch' in payload['pullrequest']:
        hook_info['pullrequest_close_source_branch'] = payload['pullrequest']['close_source_branch']
    if 'state' in payload['pullrequest']:
        if payload['pullrequest']['state'] == 'MERGED':
            hook_info['pullrequest_author'] = payload['pullrequest']['author']['display_name']
            hook_info['pullrequest_closed_by'] = payload['pullrequest']['closed_by']['display_name']
    if 'links' in payload['pullrequest'] and 'html' in payload['pullrequest']['links']:
        hook_info['pullrequest_url'] = payload['pullrequest']['links']['html']


def handle_bitbucket_actor(payload, hook_info, event_info, config):
    """Assemble information about the author of this action

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    :param str event_info: message containing information about the event, to be used to log and as feedback to user
    :param dict config: the projects configuration
    """
    event_info += ' by ' + payload['actor']['nickname']
    if 'display_name' in payload['actor']:
        event_info += f' ({payload["actor"]["display_name"]})'
    hook_info['username'] = payload['actor']['nickname']

    logger.debug(config[1])
    if 'authors' in config[1]:
        # Look up the email address in the known authors list of the project
        for author in config[1]['authors']:
            if author.lower() == hook_info['username'].lower():
                hook_info['email'] = config[1]['authors'][author]
                break
    return event_info


def handle_git_actor(payload, hook_info, event_info):
    """Assemble information about the author of this action

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    :param str event_info: message containing information about the event, to be used to log and as feedback to user
    """
    if hook_info['vcs_source'] in ('Gitea', 'Gogs'):
        event_info += ' by ' + payload['pusher']['username']
        hook_info['username'] = payload['pusher']['username']
        hook_info['email'] = payload['pusher']['email']
    elif hook_info['vcs_source'] == 'GitHub':
        event_info += ' by ' + payload['pusher']['name']
        hook_info['username'] = payload['pusher']['name']
        hook_info['email'] = payload['pusher']['email']
    return event_info


def handle_sentry_message(payload, hook_info, event_info):
    """Assemble information about the event that Sentry sent so an appropriate notification can be sent later

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    :param str event_info: message containing information about the event, to be used to log and as feedback to user
    """
    event_info += payload['project_name']
    sentry_fields = ['project_name', 'culprit', 'url', 'message']
    for field in sentry_fields:
        if field in payload:
            hook_info[field] = payload[field]
    hook_info['stacktrace'] = 'Not available'
    if 'event' in payload and payload['event'] and 'title' in payload['event']:
        hook_info['title'] = payload['event']['title']
        stacktrace = []
        if 'exception' in payload['event']:
            # Always take the last set
            frames = payload['event']['exception']['values'][-1]['stacktrace']['frames']
            for frame in frames:
                frame_message = f'*{frame["filename"]}* in *{frame["function"]}* at line *{frame["lineno"]}*'
                stacktrace.append(frame_message)
            # Sentry puts the items of the trace from last to first in the json, so reverse the trace
            stacktrace.reverse()
        elif 'logentry' in payload['event']:
            if 'message' in payload['event']['logentry']:
                stacktrace.append(payload['event']['logentry']['message'])
            if 'formatted' in payload['event']['logentry']:
                stacktrace.append(payload['event']['logentry']['formatted'])
        logger.debug(stacktrace)
        hook_info['stacktrace'] = '\\n'.join(stacktrace)
    return event_info


def get_commits_info(payload, hook_info):
    """Assemble extra information about the commits

    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    """
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


def determine_task(config, payload, hook_info, event_info):
    """Parse the incoming webhook information and assemble the hook_info

    :param dict config: the projects configuration
    :param dict payload: dictionary containing the incoming webhook payload
    :param dict hook_info: dictionary containing the webhook configuration
    :param str event_info: message containing information about the event, to be used to log and as feedback to user
    """
    if 'push' in payload:
        # BitBucket, which has a completely different format
        handle_bitbucket_push(payload, hook_info)

    if 'pullrequest' in payload:
        # BitBucket pullrequest event
        handle_bitbucket_pullrequest(payload, hook_info)

    if 'ref' in payload:
        hook_info['ref'] = payload['ref']
        if 'heads' in payload['ref']:
            hook_info['branch'] = payload['ref'].replace('refs/heads/', '')
        elif 'tags' in payload['ref']:
            hook_info['tag'] = payload['ref'].replace('refs/tags/', '')
    if 'repository' in payload:
        event_info += payload['repository']['full_name']
        hook_info['reponame'] = payload['repository']['full_name']
        if 'name' in payload['repository']:
            hook_info['project_name'] = payload['repository']['name']
    if 'actor' in payload:
        # BitBucket pusher; no email address known here though
        event_info = handle_bitbucket_actor(payload, hook_info, event_info, config)
    if 'pusher' in payload:
        # GitHub, gitea, gogs
        event_info = handle_git_actor(payload, hook_info, event_info)
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
        get_commits_info(payload, hook_info)

    logger.debug(hook_info)
    logger.info(event_info)

    return event_info
