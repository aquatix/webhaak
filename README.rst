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

Copy ``settings.py`` from example_config to the parent directory and
configure to your needs. Create a yaml file with the projects to serve (see
next section) and refer to this file from the settings.py.

Run webhaak as a service under nginx or apache and call the appropriate
url's when wanted (e.g., on push to repository).

Url's are of the form https://hook.example.com/app/<appkey>/<triggerkey>


Example configuration
---------------------

See the example `hook settings`_ for syntax of how to configure
repositories, commands and directories.

Call webhaak on its endpoint ``/getappkey`` to generate a random new key for
usage in the projects yaml file (so, for example https://hook.example.com/getappkey)

By default, webhaak clones projects in a directory under its
``REPOS_CACHE_DIR`` directory, but there is support for a per-repo parent dir
settings with ``repoparent``.

This means that webhaak then doesn't clone this repo into its default cache
dir, but in a subdirectory of the directory configured in ``repoparent``, so
<repoparent>/reponame (e.g., /srv/customparent/myproject).


Server configuration
~~~~~~~~~~~~~~~~~~~~

* `vhost for Apache2.4`_
* `uwsgi.ini`_


What's new?
-----------

See the `Changelog`_.


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
.. _Changelog: https://github.com/aquatix/webhaak/blob/master/CHANGELOG.md
