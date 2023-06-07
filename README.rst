webhaak
=======

|PyPI version| |PyPI downloads| |PyPI license| |Code quality| |Known vulnerabilities|

`webhaak`_ is a simple `webhook`_ service to update and deploy sites and do
other maintenance without having to ssh to a node.

`webhaak is on ReadTheDocs <https://webhaak.readthedocs.io/en/latest/>`_

webhaak supports ``git push`` hooks from GitHub, BitBucket, gitea, and gogs; for these it can automatically update checkouts. It also recognises Sentry notifications, and of course regular calls.


Installation
------------

From PyPI
~~~~~~~~~

Assuming you already are inside a virtualenv:

.. code-block:: bash

    pip install webhaak

From Git
~~~~~~~~

Create a new virtualenv (if you are not already in one) and install the
necessary packages:

.. code-block:: bash

    git clone https://github.com/aquatix/webhaak.git
    cd webhaak
    mkvirtualenv webhaak # or whatever project you are working on
    pip install -r requirements.txt


Usage
-----

Copy ``example.yaml`` from example_config to a directory you will use for configuration and
configure to your needs. This is a yaml file with the projects to serve (see next section).

Run webhaak as a service under nginx or apache and call the appropriate
url's when wanted (e.g., on push to repository).

webhaak can also be run from the command line: ``uvicorn webhaak:app --reload``

Be sure to export/set the ``SECRETKEY`` environment variable before running, it's needed for some management URI's.

Run ``gunicorn -k uvicorn.workers.UvicornWorker`` for production. For an example of how to set up a server `see this article <https://www.slingacademy.com/article/deploying-fastapi-on-ubuntu-with-nginx-and-lets-encrypt/>`_ with configuration for nginx, uvicorn, systemd, security and such.

The RQ background worker can be run from the command line: ``rq worker --with-scheduler``

Url's are of the form https://hook.example.com/app/<appkey>/<triggerkey>

API documentation is auto-generated, and can be browsed at https://hook.example.com/docs


Example configuration
---------------------

See the example `hook settings`_ for syntax of how to configure
repositories, commands and directories.

Call webhaak on its endpoint ``/admin/SECRETKEY/get_app_key`` to generate a random new key for
usage in the projects yaml file (so, for example https://hook.example.com/admin/abc123/get_app_key)

By default, webhaak clones projects in a directory under its
``REPOS_CACHE_DIR`` directory, but there is support for a per-repo parent dir
settings with ``repoparent`` in the yaml.

This means that webhaak then doesn't clone this repo into its default cache
dir, but in a subdirectory of the directory configured in ``repoparent``, so
<repoparent>/reponame (e.g., /srv/customparent/myproject).


Server configuration
~~~~~~~~~~~~~~~~~~~~

* `supervisord RQ worker`_ which uses the `RQ config`_
* `systemd for webhaak API`_ which uses the `gunicorn config`_
* `nginx for webhaak API`_
* `more config`_


What's new?
-----------

See the `Changelog`_.


Developing
----------

Running in PyCharm: tbd :)


.. _webhaak: https://github.com/aquatix/webhaak
.. _webhook: https://en.wikipedia.org/wiki/Webhook
.. |PyPI version| image:: https://img.shields.io/pypi/v/webhaak.svg
   :target: https://pypi.python.org/pypi/webhaak/
.. |PyPI downloads| image:: https://img.shields.io/pypi/dm/webhaak.svg
   :target: https://pypi.python.org/pypi/webhaak/
.. |PyPI license| image:: https://img.shields.io/github/license/aquatix/webhaak.svg
   :target: https://pypi.python.org/pypi/webhaak/
.. |Code quality| image:: https://api.codacy.com/project/badge/Grade/e18e62698761411482716d0fceb65bfe
   :target: https://www.codacy.com/app/aquatix/webhaak?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=aquatix/webhaak&amp;utm_campaign=Badge_Grade
   :alt: Code Quality
.. |Known vulnerabilities| image:: https://snyk.io/test/github/aquatix/webhaak/badge.svg?targetFile=requirements.txt
   :target: https://snyk.io/test/github/aquatix/webhaak
   :alt: Known vulnerabilities
.. _hook settings: https://github.com/aquatix/webhaak/blob/master/example_config/examples.yaml
.. _vhost for Apache2.4: https://github.com/aquatix/webhaak/blob/master/example_config/apache_vhost.conf
.. _uwsgi.ini: https://github.com/aquatix/webhaak/blob/master/example_config/uwsgi.ini
.. _supervisord RQ worker: https://github.com/aquatix/webhaak/blob/master/example_config/supervisord/webhaak_rq_worker.conf
.. _RQ config: https://github.com/aquatix/webhaak/blob/master/example_config/rq_settings.example.py
.. _systemd for webhaak API: https://github.com/aquatix/webhaak/blob/master/example_config/systemd/webhaak.service
.. _gunicorn config: https://github.com/aquatix/webhaak/blob/master/example_config/gunicorn_webhaak_conf.py
.. _more config: https://github.com/aquatix/webhaak/tree/master/example_config
.. _nginx for webhaak API: https://github.com/aquatix/webhaak/blob/master/example_config/nginx/hook.example.com.conf
.. _Changelog: https://github.com/aquatix/webhaak/blob/master/CHANGELOG.md
