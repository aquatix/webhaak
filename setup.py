"""
A setuptools based setup module.
See:
https://packaging.python.org/en/latest/distributing.html
https://github.com/pypa/sampleproject
"""

from setuptools import setup
# To use a consistent encoding
from codecs import open as openfile
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the relevant file
with openfile(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='webhaak',  # pip install webhaak
    description=' Simple webhook service to update and deploy sites and do other maintenance',
    # long_description=openfile('README.md', 'rt').read(),
    long_description=long_description,

    # version
    # third part for minor release
    # second when api changes
    # first when it becomes stable someday
    version='0.3.0',
    author='Michiel Scholten',
    author_email='michiel@diginaut.net',

    url='https://github.com/aquatix/webhaak',
    license='Apache',

    # as a practice no need to hard code version unless you know program wont
    # work unless the specific versions are used
    install_requires=['Flask', 'GitPython', 'python-pushover', 'pyyaml', 'requests' 'utilkit'],

    py_modules=['webhaak'],

    zip_safe=True,
)
