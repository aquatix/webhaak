# Activate virtualenv
import settings
activate_this = settings.VENV
execfile(activate_this, dict(__file__=activate_this))

from webhaak import app as application

if __name__ == "__main__":
    # application is ran standalone
    application.run()
