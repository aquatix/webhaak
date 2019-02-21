Example configuration files
===========================

Configuration file
------------------

.. literalinclude:: ../../example_config/settings.py
   :language: python
   :linenos:


Projects configuration
----------------------

This file includes the configration of which hooks are available.

.. literalinclude:: ../../example_config/examples.yaml
   :language: yaml
   :linenos:


Apache
------

.. literalinclude:: ../../example_config/apache_vhost.conf
   :language: bash
   :linenos:


uWSGI
-----

.. literalinclude:: ../../example_config/uwsgi.ini
   :language: bash
   :linenos:


Helper scripts
--------------

Updating a Flask project:

.. literalinclude:: ../../example_config/update_flask.sh
   :language: bash
   :linenos:

Updating a Python virtualenv (also used by the Flask update script):

.. literalinclude:: ../../example_config/update_virtualenv.sh
   :language: bash
   :linenos:
