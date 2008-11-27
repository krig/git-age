import ez_setup
ez_setup.use_setuptools()

from setuptools import setup, find_packages

setup(
    name = "git-age",
    version = "0.1",
    packages = find_packages(),
    zip_safe = True,

    install_requires = ["gtk+-2.0", "pygtksourceview-2.0", "pango", "gobject-2.0"],
    scripts=['git-age'],

    # metadata for upload to PyPI
    author = "Kristoffer Gronlund",
    author_email = "kristoffer.gronlund@purplescout.se",
    description = "A git-blame viewer, written using PyGTK.",
    license = "GPL",
    keywords = "hello world example examples",
    url = "http://github.com/krig/git-age/wikis",
)

