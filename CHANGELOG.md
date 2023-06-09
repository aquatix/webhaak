# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).


## TODO

- Ensure unique repo directories
- Pull request status update messages (through Telegram)


## [unreleased]

### Added

### Changed

### Removed

### Fixed


## [0.5.2] - 2023-06-09

### Fixed
- Minor error in new PushOver send function causing pushes not to be sent
- Better variable names


## [0.5.1] - 2023-06-06

### Removed
- Dependency on external PushOver library


## [0.5.0] - 2023-06-06 (FastAPI, RQ)

### Added
- Background worker through RQ (Redis Queue) for asynchronous running of jobs
- Status endpoint for a submitted job, which shows status of the (background) job, and process output
- Configuration through environment variables, including sanity checks on the values (e.g., existing paths and such); if webhaak is crashing on startup, check the last line of the stacktrace as that will likely tell what's wrong

### Changed
- API backend changed from Flask to FastAPI; big refactor
- `get_app_key` endpoint is now located behind the secret key to prevent abuse
- All configuration is done through environment variables, see [Example configuration](https://github.com/aquatix/webhaak#example-configuration) and [the configuration examples](https://github.com/aquatix/webhaak/tree/master/example_config)
- Various linting, CI and code quality improvements

### Removed
- settings.py file; configuration is handled through environment variables completely now; project/hook configuration is still in the yaml file

### Fixed
- A crash could occur when a branch was merged, as `commits` property of payload is empty (BitBucket)
- Job results when commands were failing or when a job was still running
- Lots of other small crashes and edge-cases


## [0.4.0] - 2023-02-16

### Added
- Optional endpoint to list all projects (apps) and their triggers. Enable by setting a SECRETKEY in settings.py
  The endpoint is located at /admin/<secretkey>/list and [webhaak-ui](https://github.com/aquatix/webhaak-ui) is an example client.
- Possibility to remotely use the endpoints (e.g., through jsonp)
- Git-based version string generation, use `REPOVERSION` placeholder in your projects yaml
- [ReadTheDocs](https://webhaak.readthedocs.io/en/latest/), including apiDoc
- Support for Gitea and Gogs webhooks
- Support for checking out a certain branch (use `branch: <name>`)
- Notification also includes runtime of the Git update and command execution
- Gather information about the webhook (push) event, to be used by commands, with keyword substitution
- BitBucket support

### Changed
- Changed yaml library to strictyaml, to be safer
- Better logging
- Fixed 'REPODIR' substitution in commands
- Fixes for wrong payload lookups on pings and some other crashes

### Removed
- pyyaml dependency
- utilkit dependency


## [0.3.0] - 2018-10-01

### Added
- Python 3 compatibility
- Subprocess worker, making requests process the pull/clone and script execution asynchronous from the API call
- Give more output about the checkout and pull in the response JSON
- PushOver integration, providing feedback after the subprocess is done with updating and running its job

### Changed
- Fixes to the example
- Better logging of the results of the jobs
- Use subprocess.run() instead of check_output()

### Removed
- Trial with Redis
- Trial with Quart (asyncio Flask-alike)


## [0.2.0] - 2016-05-13 (Friday the 13th)

### Added
- wsgi file, for Apache mod_wsgi and supervisord and such
- Example Apache vhost
- Support for GitHub ping and push requests
- Support for per-repo parent dir settings with `repoparent`.
  This means that webhaak doesn't clone this repo into its default cache dir, but in a subdirectory of
  the directory configured in `repoparent`, so <repoparent>/reponame (e.g., /srv/customparent/myproject)
- json response now includes project name
- Logs information about GitHub hook requests

### Changed
- Better error messags in json responses
- More info in json response about the executed command
- If DEBUG=True, Flask's DEBUG is enabled too
- Correctly checkout projects, don't only fetch the repos
- Better repo directory generation
- Better execution of scripts and other commands (with parameters)


## [0.1.0] - 2016-05-05 (Initial release)

### Added
- Initial release
- Flask API with appkey-secured endpoints to:
  - Git clone/fetch a repo to a certain directory
  - Run a pre-defined command
  - Return result of both the repo update and command execution as json response
- Configuration through yaml file: actions are pre-defined
- /getappkey helper endpoint to generate keys for usage in the yaml configuration
