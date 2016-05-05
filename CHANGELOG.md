## v0.1.0 (unreleased)

2016-05-??

- Initial release
- Flask API with appkey-secured endpoints to:
  - Git clone/fetch a repo to a certain directory
  - Run a pre-defined command
  - Return result of both the repo update and command execution as json response
- Configuration through yaml file: actions are pre-defined
- /getappkey helper endpoint to generate keys for usage in the yaml configuration
