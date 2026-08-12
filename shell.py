#!/usr/bin/env python3
"""
TheDawg native shell — GTK4 + libadwaita + WebKitGTK 6.0
========================================================
A real desktop application window, not a browser in a costume.

The old front door shelled out to Chromium with `--app=`, which meant: a second
browser profile on disk, a multi-hundred-megabyte process tree, a generic icon in
the Plasma task switcher unless you got `--class` exactly right, and no way to
integrate with the desktop at all.

This replaces it with a GTK4 window that owns a WebKitGTK view. Consequences:

  * one process, ~150 MB instead of ~500 MB
  * a REAL titlebar (Adw.HeaderBar) that follows the system theme, so it looks
    correct on KDE Plasma 6 and on GNOME without any per-desktop hacks
  * a proper Wayland `app_id` / X11 WM_CLASS, so the icon, the task switcher and
    window-rule matching all work
  * native accelerators, native window state, native scaling
  * no Chromium required, and nothing written to a browser profile

Requires (CachyOS / Arch):
    sudo pacman -S --needed python-gobject gtk4 libadwaita webkit2gtk-6.0

Import-safe: `available()` never raises, so thedawg.py can probe for the shell and
fall back to a browser window if the bindings aren't installed.
"""

import json
import os
import sys
from pathlib import Path

APP_ID = "io.github.the_priest.TheDawg"

_MIN_W, _MIN_H = 900, 620
_DEF_W, _DEF_H = 1360, 900


# ---------------------------------------------------------------- capability --
def available():
    """True if GTK4 + WebKitGTK 6.0 + libadwaita can actually be imported.

    Cheap enough to call on every launch and guaranteed not to raise: a missing
    typelib is a normal condition here, not an error.
    """
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gtk, Adw, WebKit  # noqa: F401
        return True
    except Exception:
        return False


def missing_reason():
    """A one-line explanation of why the native shell is unavailable."""
    try:
        import gi
    except Exception:
        return "PyGObject (python-gobject) is not installed"
    for ns, ver, pkg in (("Gtk", "4.0", "gtk4"),
                         ("Adw", "1", "libadwaita"),
                         ("WebKit", "6.0", "webkit2gtk-6.0")):
        try:
            gi.require_version(ns, ver)
            __import__("gi.repository", fromlist=[ns])
        except Exception:
            return f"{ns} {ver} typelib is missing ({pkg})"
    return "unknown"


# ------------------------------------------------------------ window state ---
def _state_path(config_dir):
    return Path(config_dir) / "window.json"


def _load_state(config_dir):
    try:
        with open(_state_path(config_dir), encoding="utf-8") as f:
            s = json.load(f)
        return {
            "w": max(_MIN_W, int(s.get("w", _DEF_W))),
            "h": max(_MIN_H, int(s.get("h", _DEF_H))),
            "maximized": bool(s.get("maximized", False)),
            "zoom": min(2.5, max(0.6, float(s.get("zoom", 1.0)))),
        }
    except Exception:
        return {"w": _DEF_W, "h": _DEF_H, "maximized": False, "zoom": 1.0}


def _save_state(config_dir, state):
    try:
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        tmp = _state_path(config_dir).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, _state_path(config_dir))
    except Exception:
        pass


# ------------------------------------------------------------------- shell ---
def run(url, config_dir, data_dir, icon_dir=None, on_quit=None, dev=False):
    """Open TheDawg in a native GTK4 window and block until it is closed.

    url        -- the local server address to load
    config_dir -- where window geometry is remembered
    data_dir   -- where the web view keeps its cache and local storage
    icon_dir   -- directory holding icon.svg / icon-256.png, used as a fallback
                  when the icon isn't installed into the hicolor theme yet
    on_quit    -- called once, on the way out, before the process exits
    dev        -- open with developer tools enabled

    Returns True if the shell ran, False if the bindings were unavailable (in
    which case the caller should fall back to a browser window).
    """
    if not available():
        return False

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gtk, Adw, WebKit, Gio, GLib, Gdk

    state = _load_state(config_dir)

    # Register the bundled icon directory so the window and the task switcher can
    # find the Dawg even before `install.sh` has copied it into hicolor.
    if icon_dir and os.path.isdir(icon_dir):
        try:
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.add_search_path(icon_dir)
        except Exception:
            pass

    class DawgWindow(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app)
            self.set_title("TheDawg")
            self.set_icon_name(APP_ID)
            self.set_default_size(state["w"], state["h"])
            self.set_size_request(_MIN_W, _MIN_H)
            if state["maximized"]:
                self.maximize()

            # --- web view -----------------------------------------------------
            # Keep cache and local storage inside TheDawg's own data dir rather
            # than polluting the user's browser profile.
            cache = str(Path(data_dir) / "webkit")
            Path(cache).mkdir(parents=True, exist_ok=True)
            try:
                session = WebKit.NetworkSession(data_directory=cache,
                                                cache_directory=cache)
            except Exception:
                session = None

            self.web = WebKit.WebView(network_session=session) if session else WebKit.WebView()
            self.web.set_vexpand(True)
            self.web.set_hexpand(True)

            s = self.web.get_settings()
            self._set(s, "set_enable_developer_extras", True)
            self._set(s, "set_enable_smooth_scrolling", True)
            self._set(s, "set_enable_page_cache", True)
            # the startup chime should just play; there is no browser tab here for
            # the "user gesture required" policy to be protecting
            self._set(s, "set_media_playback_requires_user_gesture", False)
            self._set(s, "set_javascript_can_access_clipboard", True)
            self._set(s, "set_enable_back_forward_navigation_gestures", False)
            self._set(s, "set_enable_html5_database", True)
            self._set(s, "set_enable_html5_local_storage", True)
            # GPU compositing on. WEBKIT_DISABLE_DMABUF_RENDERER=1 in the
            # environment is the escape hatch when a driver dislikes it.
            try:
                s.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.ALWAYS)
            except Exception:
                pass
            # match the desktop's own fonts so the UI doesn't look imported
            self._set(s, "set_default_font_family", "system-ui")
            self._set(s, "set_monospace_font_family", "JetBrains Mono")

            # transparent page background: the GTK window paints beneath it, so
            # the app picks up the system accent/backdrop instead of a hard white
            # flash while the first paint lands
            try:
                self.web.set_background_color(Gdk.RGBA())
            except Exception:
                pass

            self.web.set_zoom_level(state["zoom"])
            # `?native=1` tells the page to drop its fake titlebar — this window
            # already has a real one.
            sep = "&" if "?" in url else "?"
            self.web.load_uri(f"{url}{sep}native=1")

            # links to real websites open in the user's browser, not in here
            self.web.connect("decide-policy", self._on_policy)
            # `window.open(...)` / target="_blank" asks WebKit to CREATE a second
            # web view. With nothing connected to "create", WebKit makes none and
            # the click does nothing at all — which is why "⤓ download log" was
            # silently dead in the native window while working fine in a browser.
            self.web.connect("create", self._on_create)
            # A download started from the page (the ⤓ log button builds a Blob and
            # clicks an <a download>) reaches WebKit as a WebKitDownload, and
            # WebKit will NOT pick a destination for you: with nothing connected
            # to `download-started` / `decide-destination` the download is
            # cancelled and the click does nothing. 2.2.3 swapped window.open()
            # for the Blob to fix this button — which fixed it in the browser
            # fallback only. This is the other half.
            try:
                sess = self.web.get_network_session()
                if sess is not None:
                    sess.connect("download-started", self._on_download)
            except Exception:
                pass

            # --- header bar ---------------------------------------------------
            head = Adw.HeaderBar()
            head.add_css_class("flat")
            title = Adw.WindowTitle(title="TheDawg", subtitle="python toolsmith")
            head.set_title_widget(title)
            self.title_widget = title

            menu = Gio.Menu()
            view = Gio.Menu()
            view.append("Zoom in", "win.zoom-in")
            view.append("Zoom out", "win.zoom-out")
            view.append("Reset zoom", "win.zoom-reset")
            menu.append_section(None, view)
            other = Gio.Menu()
            other.append("Reload", "win.reload")
            other.append("Open in browser", "win.browser")
            other.append("Developer tools", "win.inspector")
            menu.append_section(None, other)
            quit_sec = Gio.Menu()
            quit_sec.append("Quit", "win.quit")
            menu.append_section(None, quit_sec)

            btn = Gtk.MenuButton()
            btn.set_icon_name("open-menu-symbolic")
            btn.set_menu_model(menu)
            btn.set_tooltip_text("Menu")
            head.pack_end(btn)

            reload_btn = Gtk.Button(icon_name="view-refresh-symbolic")
            reload_btn.set_tooltip_text("Reload  (Ctrl+R)")
            reload_btn.add_css_class("flat")
            reload_btn.connect("clicked", lambda *_: self.web.reload())
            head.pack_start(reload_btn)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(head)
            box.append(self.web)
            self.set_content(box)

            self._install_actions()
            self.connect("close-request", self._on_close)
            if dev:
                GLib.timeout_add(600, self._open_inspector)

        # -- helpers -----------------------------------------------------------
        @staticmethod
        def _set(obj, method, value):
            """Call a WebKit setting if this version has it. The 6.0 API still
            moves; a missing setter should never take the whole app down."""
            fn = getattr(obj, method, None)
            if fn is None:
                return
            try:
                fn(value)
            except Exception:
                pass

        def _install_actions(self):
            app = self.get_application()

            def add(name, fn, *accels):
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", lambda *_: fn())
                self.add_action(act)
                if accels:
                    app.set_accels_for_action(f"win.{name}", list(accels))

            add("reload", lambda: self.web.reload(), "<Ctrl>r", "F5")
            add("zoom-in", lambda: self._zoom(+0.1), "<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add")
            add("zoom-out", lambda: self._zoom(-0.1), "<Ctrl>minus", "<Ctrl>KP_Subtract")
            add("zoom-reset", lambda: self._zoom(None), "<Ctrl>0")
            add("inspector", self._open_inspector, "<Ctrl><Shift>i", "F12")
            add("browser", self._open_browser)
            add("fullscreen", self._toggle_fullscreen, "F11")
            add("quit", self.close, "<Ctrl>q", "<Ctrl>w")

        def _zoom(self, delta):
            z = 1.0 if delta is None else self.web.get_zoom_level() + delta
            z = min(2.5, max(0.6, round(z, 2)))
            self.web.set_zoom_level(z)
            state["zoom"] = z

        def _toggle_fullscreen(self):
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()

        def _open_inspector(self):
            try:
                insp = self.web.get_inspector()
                if insp:
                    insp.show()
            except Exception:
                pass
            return False

        def _open_browser(self):
            try:
                Gio.AppInfo.launch_default_for_uri(url, None)
            except Exception:
                pass

        def _on_download(self, session, download):
            """Give every download a real destination, in the user's Downloads."""
            def _dest(dl, suggested):
                try:
                    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) \
                        or str(Path.home() / "Downloads")
                    Path(d).mkdir(parents=True, exist_ok=True)
                    name = suggested or "download"
                    target = Path(d) / name
                    stem, suf, n = target.stem, target.suffix, 1
                    while target.exists() and n < 500:
                        target = Path(d) / f"{stem}-{n}{suf}"
                        n += 1
                    dl.set_destination(target.as_uri())
                except Exception:
                    return False
                return True

            def _done(dl):
                try:
                    self.title_widget.set_subtitle("saved to Downloads")
                    GLib.timeout_add_seconds(
                        4, lambda: (self.title_widget.set_subtitle("python toolsmith"), False)[1])
                except Exception:
                    pass

            try:
                download.connect("decide-destination", _dest)
                download.connect("finished", _done)
            except Exception:
                pass

        def _on_create(self, web, nav_action):
            """Hand a popup request to the right place instead of dropping it."""
            try:
                uri = nav_action.get_request().get_uri() or ""
            except Exception:
                uri = ""
            if not uri:
                return None
            if uri.startswith(url):
                # our own server (a file download, the log) — load it here rather
                # than bouncing it out to a browser that isn't holding the session
                self.web.load_uri(uri)
            else:
                try:
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except Exception:
                    pass
            return None

        def _on_policy(self, web, decision, dtype):
            """Keep the app window on the local server; send everything else out
            to the real browser, where a normal link belongs."""
            try:
                if dtype != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                    return False
                nav = decision.get_navigation_action()
                uri = nav.get_request().get_uri() or ""
                if uri.startswith(url) or uri.startswith("about:") or uri.startswith("data:"):
                    return False
                if uri.startswith("http://") or uri.startswith("https://"):
                    decision.ignore()
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                    return True
            except Exception:
                pass
            return False

        def _on_close(self, *_):
            try:
                w, h = self.get_default_size()
                _save_state(config_dir, {"w": w, "h": h,
                                         "maximized": self.is_maximized(),
                                         "zoom": self.web.get_zoom_level()})
            except Exception:
                pass
            if on_quit:
                try:
                    on_quit()
                except Exception:
                    pass
            return False

    class DawgApp(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.NON_UNIQUE)

        def do_activate(self):
            win = self.props.active_window or DawgWindow(self)
            win.present()

    # Prefer the system theme; TheDawg's own UI is dark, and a dark header bar
    # keeps the window from looking half-lit.
    try:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
    except Exception:
        pass

    app = DawgApp()
    app.run([])
    return True


if __name__ == "__main__":
    # standalone: `python3 shell.py http://127.0.0.1:8765`
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
    if not available():
        print("native shell unavailable:", missing_reason())
        raise SystemExit(1)
    cfg = Path.home() / ".config" / "thedawg"
    dat = Path.home() / ".local" / "share" / "thedawg"
    run(target, str(cfg), str(dat), icon_dir=str(Path(__file__).parent / "assets"), dev=True)
