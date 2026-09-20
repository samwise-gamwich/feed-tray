const Gio = imports.gi.Gio;
const GLib = imports.gi.GLib;
const GObject = imports.gi.GObject;
const St = imports.gi.St;
const Clutter = imports.gi.Clutter;

const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;

const TRACKER = GLib.build_filenamev([
    GLib.get_home_dir(), '.local', 'bin', 'feed-tracker.py'
]);
const STATUS_FILE = GLib.build_filenamev([
    GLib.get_home_dir(), '.local', 'state', 'feed-notify', 'status.json'
]);
const REFRESH_SECS = 60;

const FeedIndicator = GObject.registerClass(
class FeedIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'RSS Feeds');

        this._box = new St.BoxLayout({
            style_class: 'panel-status-menu-box feed-tray-box',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._icon = new St.Icon({
            icon_name: 'applications-internet-symbolic',
            style_class: 'system-status-icon',
        });
        this._label = new St.Label({
            text: 'Feeds',
            style_class: 'feed-tray-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._badge = new St.Label({
            text: '',
            style_class: 'feed-tray-badge',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._box.add_child(this._icon);
        this._box.add_child(this._label);
        this._box.add_child(this._badge);
        this.add_child(this._box);

        // always fetch the latest feeds when the menu is opened
        this.menu.connect('about-to-show', () => this._fetchLatest());
        this._rebuild();
    }

    _fetchLatest() {
        try {
            const proc = Gio.Subprocess.new([TRACKER], Gio.SubprocessFlags.NONE);
            proc.wait_async(null, (p, res) => {
                p.wait_finish(res);
                this._rebuild();
            });
        } catch (e) {
            logError(e, 'Feed Tray: latest-fetch failed');
        }
    }

    _read() {
        try {
            const [ok, bytes] = GLib.file_get_contents(STATUS_FILE);
            if (ok)
                return JSON.parse(new TextDecoder().decode(bytes));
        } catch (e) {
            logError(e, 'Feed Tray: failed to read status.json');
        }
        return {};
    }

    _rebuild() {
        const data = this._read();
        this.menu.removeAll();
        let total = 0, todayCount = 0;
        for (const feed of Object.values(data)) {
            total += (feed.items || []).length;
            todayCount += (feed.today || []).length;
        }

        if (total > 0) {
            this.add_style_class_name('has-new');
            this._badge.set_text(`${total}`);
        } else {
            this.remove_style_class_name('has-new');
            this._badge.set_text('');
        }

        if (todayCount === 0) {
            const empty = new PopupMenu.PopupMenuItem('No new items',
                { reactive: false });
            this.menu.addMenuItem(empty);
            return;
        }

        for (const [url, feed] of Object.entries(data)) {
            const items = feed.items || [];
            const today = feed.today || [];

            // merge: all unread first, then today's read items, then the
            // feed's latest entry so every feed always shows >= 1 item
            const seenTitles = new Set();
            const rows = [];
            for (const it of [...items, ...today,
                              ...(feed.latest ? [feed.latest] : [])]) {
                if (seenTitles.has(it.title))
                    continue;
                seenTitles.add(it.title);
                rows.push(it);
            }
            if (rows.length === 0)
                continue;

            const sub = new PopupMenu.PopupSubMenuMenuItem(
                `${feed.name}  (${items.length} new)`);

            const section = new PopupMenu.PopupMenuSection();
            const scroll = new St.ScrollView({
                style_class: 'feed-tray-scroll',
                overlay_scrollbars: true,
            });
            scroll.add_actor(section.actor);
            const scrollSection = new PopupMenu.PopupMenuSection();
            scrollSection.actor.add_actor(scroll);
            sub.menu.addMenuItem(scrollSection);

            rows.forEach((it) => {
                const label = (it.read ? '' : '* ') +
                    (it.title.length > 72
                        ? it.title.slice(0, 72) + '…' : it.title);
                const item = new PopupMenu.PopupMenuItem(label);
                item.connect('activate', () => {
                    this.menu.close();
                    // mark read, AI-summarize into markdown, open it
                    GLib.spawn_command_line_async(
                        `${TRACKER} --summarize ${JSON.stringify(url)} ` +
                        `${JSON.stringify(it.title)}`);
                });
                section.addMenuItem(item);
            });

            if (items.length > 0) {
                sub.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());                const clear = new PopupMenu.PopupMenuItem('Mark all read');
                clear.connect('activate', () => {
                    GLib.spawn_command_line_async(
                        `${TRACKER} --clear ${JSON.stringify(url)}`);
                });
                sub.menu.addMenuItem(clear);
            }

            this.menu.addMenuItem(sub);
        }
    }
});

const FeedTrayExtension = GObject.registerClass(
class FeedTrayExtension extends GObject.Object {
    _init() {
        super._init();
        this._indicator = null;
        this._timeoutId = null;
    }

    enable() {
        this._indicator = new FeedIndicator();
        Main.panel.addToStatusArea('feed-tray', this._indicator, 0, 'right');
        this._timeoutId = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT, REFRESH_SECS,
            () => {
                this._indicator._rebuild();
                return GLib.SOURCE_CONTINUE;
            });
    }

    disable() {
        if (this._timeoutId) {
            GLib.Source.remove(this._timeoutId);
            this._timeoutId = null;
        }
        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
    }
});

function init() {
    return new FeedTrayExtension();
}
