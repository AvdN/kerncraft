from __future__ import absolute_import
from setuptools import setup, find_packages  # Always prefer setuptools over distutils
from codecs import open  # To use a consistent encoding
from os import path

here = path.abspath(path.dirname(__file__))

# Get the long description from the relevant file
with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='kerncraft',

    # Versions should comply with PEP440.  For a discussion on single-sourcing
    # the version across setup.py and the project code, see
    # https://packaging.python.org/en/latest/single_source_version.html
    version='0.2.1',

    description='Loop Kernel Analysis and Performance Modeling Toolkit',
    long_description=long_description,

    # The project's main homepage.
    url='https://github.com/cod3monk/kerncraft',

    # Author details
    author='Julian Hammer',
    author_email='julian.hammer@fau.de',

    # Choose your license
    license='AGPLv3',

    # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        # How mature is this project? Common values are
        #   3 - Alpha
        #   4 - Beta
        #   5 - Production/Stable
        'Development Status :: 4 - Beta',

        # Indicate who your project is intended for
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering',
        'Topic :: Software Development',
        'Topic :: Utilities',

        # Pick your license as you wish (should match "license" above)
        'License :: OSI Approved :: GNU Affero General Public License v3',

        # Specify the Python versions you support here. In particular, ensure
        # that you indicate whether you support Python 2, Python 3 or both.
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
    ],

    # What does your project relate to?
    keywords='hpc performance benchmark analysis',

    # You can just specify the packages manually here if your project is
    # simple. Or you can use find_packages().
    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),

    # List run-time dependencies here.  These will be installed by pip when your
    # project is installed. For an analysis of "install_requires" vs pip's

    # https://packaging.python.org/en/latest/requirements.html
    install_requires=[
        'ruamel.yaml',
        'six',
        'sympy>0.7.6.1',
        'pycachesim',
        'pylru',
        'numpy',
    ],

    # List additional groups of dependencies here (e.g. development dependencies).
    # You can install these using the following syntax, for example:
    # $ pip install -e .[dev,test]
    extras_require={
        'plot': ['matplotlib'],
        'test': ['requests'],
    },

    # If there are data files included in your packages that need to be
    # installed, specify them here.  If using Python 2.6 or less, then these
    # have to be included in MANIFEST.in as well.
    package_data={
        'kerncraft': ['headers/dummy.c', 'headers/kerncraft.h', 'README.rst', 'LICENSE'],
        'examples': [
            'machine-files/*.yaml',
            'kernels/*.c',
            'kernels/*.testcases'],
        'tests': ['test_files/*.c', 'test_files/*.yaml', '*.py'],
    },
    include_package_data=True,

    # Although 'package_data' is the preferred approach, in some case you may
    # need to place data files outside of your packages.
    # see http://docs.python.org/3.4/distutils/setupscript.html#installing-additional-files
    # In this case, 'data_file' will be installed into '<sys.prefix>/my_data'
    # data_files=[('my_data', ['data/data_file'])],

    # To provide executable scripts, use entry points in preference to the
    # "scripts" keyword. Entry points provide cross-platform support and allow
    # pip to create the appropriate form of executable for the target platform.
    entry_points={
        'console_scripts': [
            'kerncraft=kerncraft.kerncraft:main',
            'iaca_marker=kerncraft.iaca_marker:main',
            'likwid_bench_auto=kerncraft.likwid_bench_auto:main',
            'picklemerge=kerncraft.picklemerge:main'
        ],
    },
)
