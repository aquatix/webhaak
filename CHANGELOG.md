## v0.2.0 (unreleased)

2016-05-??

- wsgi file, for Apache mod_wsgi and supervisord and such
- Better error messags in json responses
- Example Apache vhost
- More info in json response about the executed command
- If DEBUG=True, Flask's DEBUG is enabled too
- Support for GitHub ping and push requests
- Correctly checkout projects, don't only fetch the repos
- Support for per-repo parent dir settings with `repoparent`.
  This means that webhaak doesn't clone this repo into its default cache dir, but in a subdirectory of
  the directory configured in `repoparent`, so <repoparent>/reponame (e.g., /srv/customparent/myproject)
- json response now includes project name
- Logs information about GitHub hook requests
- Better repo directory generation
- Better execution of scripts and other commands (with parameters)


## v0.1.0

2016-05-05

- Initial release
- Flask API with appkey-secured endpoints to:
  - Git clone/fetch a repo to a certain directory
  - Run a pre-defined command
  - Return result of both the repo update and command execution as json response
- Configuration through yaml file: actions are pre-defined
- /getappkey helper endpoint to generate keys for usage in the yaml configuration
