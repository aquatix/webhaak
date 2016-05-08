webhaak
=======

|PyPI version| |PyPI downloads| |PyPI license| |Code health|

`webhaak`_ is a simple `webhook`_ service to update and deploy sites and do
other maintenance without having to ssh to a node.


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


Example configuration
---------------------

`Hook settings`_


Server configuration
^^^^^^^^^^^^^^^^^^^^

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
.. |Code health| image:: https://landscape.io/github/aquatix/webhaak/master/landscape.svg?style=flat
   :target: https://landscape.io/github/aquatix/webhaak/master
   :alt: Code Health
.. _hook settings: https://github.com/aquatix/webhaak/blob/master/example_config/examples.yaml
.. _vhost for Apache2.4: https://github.com/aquatix/webhaak/blob/master/example_config/apache_vhost.conf
.. _uwsgi.ini: https://github.com/aquatix/webhaak/blob/master/example_config/uwsgi.ini
.. _Changelog: https://github.com/aquatix/webhaak/blob/master/CHANGELOG.md
