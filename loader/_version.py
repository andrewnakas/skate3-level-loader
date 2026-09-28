"""The one place the version is written down.

The release tag is `v` + this. CI reads it back out to name artifacts, so a tag
that disagrees with the source is a build failure rather than a mislabelled zip.
"""

__version__ = "0.1.6"
