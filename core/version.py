"""
Single source of truth for the app version/release label.

Fed by bump_version.py, which reads the real system clock -- same fix
the epub tool needed twice (v12 promised to "check manually," which
didn't hold; v25 built an actual mechanism reading the clock). Starting
with the real mechanism here rather than the promise-based version.
"""

APP_VERSION = "2026-09-03#08"
RELEASE_LABEL = "v0.1"
