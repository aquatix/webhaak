Example configuration files
===========================

Examples of various configuration possibilities to help you get started. The `source scripts are available too <https://github.com/aquatix/webhaak/tree/master/example_config>`_.


Projects configuration
----------------------

This file includes the configration of which hooks are available.

.. literalinclude:: ../../example_config/examples.yaml
   :language: yaml
   :linenos:


SystemD unit file for webhaak webservice
----------------------------------------

.. literalinclude:: ../../example_config/systemd/webhaak.service
   :language: ini
   :linenos:


Supervisord for RQ worker
-------------------------

.. literalinclude:: ../../example_config/supervisord/webhaak_rq_worker.conf
   :language: ini
   :linenos:


Environment file
----------------

The various environment variables have to be put in the supervisord config and systemd unit file like described above; they can also be put in a .env file.

.. literalinclude:: ../../example_config/example.env
   :language: ini
   :linenos:


Helper scripts
--------------

Updating a webapp project (like a FastAPI one):

.. literalinclude:: ../../example_config/update_webapp.sh
   :language: bash
   :linenos:

Updating a Flask project:

.. literalinclude:: ../../example_config/update_flask.sh
   :language: bash
   :linenos:

Updating a Python virtualenv (also used by the Flask update script):

.. literalinclude:: ../../example_config/update_virtualenv.sh
   :language: bash
   :linenos:


Running flake8 linter and isort checker over a directory with Python files:

.. literalinclude:: ../../example_config/flake8diff.sh
   :language: bash
   :linenos:


Apache
------

Not officially supported anymore since the rewrite with FastAPI.


uWSGI
-----

Not officially supported anymore since the rewrite with FastAPI.
