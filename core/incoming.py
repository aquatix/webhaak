import logging

logger = logging.getLogger('webhaak')


def determine_task(config, payload, hook_info, event_info):
    """Parse the incoming webhook information and assemble the hook_info

    :param dict config:
    """
    sentry_message = False

    if 'push' in payload:
        # BitBucket, which has a completely different format
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

        if 'links' in payload['push']['changes'][0]:
            hook_info['compare_url'] = payload['push']['changes'][0]['links']['html']['href']
        elif payload['push']['changes'][0]['old'] and 'links' in payload['push']['changes'][0]['old']:
            hook_info['compare_url'] = payload['push']['changes'][0]['old']['links']['html']['href']
        else:
            hook_info['compare_url'] = ''

        hook_info['commits'] = []
        for commit in payload['push']['changes'][0]['commits']:
            commit_info = {'hash': commit['hash']}
            if 'user' in commit['author']:
                if 'username' in commit['author']['user']:
                    commit_info['name'] = commit['author']['user']['username']
                else:
                    commit_info['name'] = commit['author']['user']['nickname']
            commit_info['email'] = commit['author']['raw']
            hook_info['commits'].append(commit_info)

    if 'pullrequest' in payload:
        # BitBucket pullrequest event
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
        event_info += ' by ' + payload['actor']['nickname']
        if 'display_name' in payload['actor']:
            event_info += ' ({})'.format(payload['actor']['display_name'])
        hook_info['username'] = payload['actor']['nickname']

        logger.debug(config[1])
        if 'authors' in config[1]:
            # Look up the email address in the known authors list of the project
            for author in config[1]['authors']:
                if author.lower() == hook_info['username'].lower():
                    hook_info['email'] = config[1]['authors'][author]
                    break
    if 'pusher' in payload:
        if hook_info['vcs_source'] in ('Gitea', 'Gogs'):
            event_info += ' by ' + payload['pusher']['username']
            hook_info['username'] = payload['pusher']['username']
            hook_info['email'] = payload['pusher']['email']
        elif hook_info['vcs_source'] == 'GitHub':
            event_info += ' by ' + payload['pusher']['name']
            hook_info['username'] = payload['pusher']['name']
            hook_info['email'] = payload['pusher']['email']
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
    if sentry_message:
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
                    frame_message = '*{}* in *{}* at line *{}*'.format(
                        frame['filename'],
                        frame['function'],
                        frame['lineno']
                    )
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
    logger.debug(hook_info)
    logger.info(event_info)

    return event_info
