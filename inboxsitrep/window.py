# Copyright (C) 2026 miralemalic
# SPDX-License-Identifier: MIT

# A window of its own for the dashboard.  pywebview when it is installed
# (pip install "inboxsitrep[window]"; macOS and Windows use it); on Linux,
# GTK and WebKit straight from the system; failing both, Chrome or Edge in
# app mode, which is still a window without tabs or an address bar.

import os
import shutil
import subprocess
import webbrowser


def gtk(url, title, width, height):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gtk, WebKit2
    win = Gtk.Window(title=title)
    win.set_default_size(width, height)
    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_enable_developer_extras(False)
    home = url.split('/?')[0]

    def outside(view, decision, kind):
        # A link out of the app (Slack's app page, say) opens in the
        # browser, not in this window.
        if kind in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            target = decision.get_navigation_action().get_request().get_uri()
            if not target.startswith(home):
                webbrowser.open(target)
                decision.ignore()
                return True
        return False
    view.connect('decide-policy', outside)
    view.load_uri(url)
    win.add(view)
    win.connect('destroy', Gtk.main_quit)
    win.show_all()
    Gtk.main()


BROWSERS = ['chromium-browser', 'chromium', 'google-chrome', 'microsoft-edge', 'msedge',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Google\Chrome\Application\chrome.exe']


def browser(url):
    for name in BROWSERS:
        exe = shutil.which(name) or (name if os.path.isabs(name) and os.path.exists(name) else None)
        if exe:
            # --app gives a bare window; it returns when the window closes
            # only if it started a new browser process, so don't rely on it.
            subprocess.Popen([exe, '--app=' + url, '--new-window'])
            return False
    webbrowser.open(url)
    return False


def open_window(url, title='Inbox Sitrep', width=1440, height=920):
    # Returns True when it blocked until the window was closed.
    try:
        import webview
        webview.create_window(title, url, width=width, height=height)
        webview.start()
        return True
    except ImportError:
        pass
    try:
        gtk(url, title, width, height)
        return True
    except (ImportError, ValueError):
        return browser(url)
