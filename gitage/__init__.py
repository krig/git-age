#!/usr/bin/env python
# git-age  Copyright (C) 2008  Kristoffer Gronlund
#    This program comes with ABSOLUTELY NO WARRANTY; for details see LICENSE.
#    This is free software, and you are welcome to redistribute it
#    under certain conditions; see LICENSE for details.
#
# a git blame viewer
# shows the file with annotations
# on author and age etc. per line.
#
import sys, subprocess
import re
import gravatar
import threading

import pygtk
pygtk.require('2.0')
import gtk
import gobject
import pango
import gtksourceview2
import time
import Queue
import platform

class GravatarLoader(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self._inqueue = Queue.Queue()
        self._outqueue = Queue.Queue()
        self.gravatars = {}
        self.latest_job = None

    def run(self):
        while True:
            try:
                job = self._inqueue.get()
                if not job: continue
                item = gravatar.get(job)
                if not item: continue
                self._outqueue.put((job, item))
            except Queue.Empty:
                pass

    def sync_update(self):
        try:
            job, item = self._outqueue.get(block=False)
            if job:
                self.gravatars[job] = item
        except Queue.Empty:
            pass
    def query(self, job = None):
        if not job:
            if self.latest_job:
                job = self.latest_job
            else:
                return None
        item = self.gravatars.get(job)
        if item:
            if job == self.latest_job:
                self.latest_job = None
            return item
        if self.latest_job != job:
            self._inqueue.put(job)
            self.latest_job = job
        return None

class BlamedFile(object):
    class Commit(object):
        def __init__(self, sha1):
            self.sha1 = sha1
        def __repr__(self):
            return "<%s %s>"%(self.__class__.__name__,
                              ", ".join("%s = %s" % (key, value) for key, value in self.__dict__.iteritems()))

    class Line(object):
        def __init__(self, fileline, commit, sourceline, resultline, num_lines):
            self.text = fileline
            self.commit = commit
            self.sourceline = sourceline
            self.resultline = resultline
            self.num_lines = num_lines
        def __repr__(self):
            return "<Line (%s/%d/%s) %s>" % (self.sourceline, self.resultline, self.num_lines, self.commit)

    def __init__(self, fil, view):
        self.sha1_to_commit = {}
        self.commits = []
        self.lines = []
        self.view = view
        self.text = ''
        try:
            sys.stdout.write("\rparsing %s..." % (fil))
            sys.stdout.flush()
            self.filelines = open(fil).readlines()
        except IOError:
            sys.stderr.write("Unable to open %s!\n"%(fil))
            sys.exit(1)

        self.text = "".join(self.filelines)
        sys.stdout.write("\rgit blame --incremental %s" % (fil))
        sys.stdout.flush()
        p = subprocess.Popen(["git", "blame", "--incremental", fil],
                             shell=False,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        beginline = re.compile(r'(\w{40})\s+(\d+)\s+(\d+)\s+(\d+)')
        currcommit = None
        for line in p.stdout:
            bgm = beginline.match(line)
            if bgm:
                sys.stdout.write("\r%s" % (line.strip()))
                sys.stdout.flush()
                sha1 = bgm.group(1)
                if self.sha1_to_commit.has_key(sha1):
                    currcommit = self.sha1_to_commit[sha1]
                else:
                    currcommit = BlamedFile.Commit(sha1)
                    self.commits.append(currcommit)
                    self.sha1_to_commit[sha1] = currcommit
                sourceline = int(bgm.group(2))
                resultline = int(bgm.group(3))
                num_lines = int(bgm.group(4))
                blameline = BlamedFile.Line(self.filelines[resultline-1], currcommit, sourceline, resultline, num_lines)
                for _ in range(num_lines):
                    self.lines.append(blameline)
            elif currcommit:
                # parse metadata about blameline
                cmd, _, data = line.partition(' ')
                data = data.strip()
                cmd = cmd.replace('-', '_')

                if cmd == 'author_time' or cmd == 'committer_time':
                    data = int(data)

                if hasattr(currcommit, cmd):
                    assert getattr(currcommit, cmd) == data
                setattr(currcommit, cmd, data)
        sys.stdout.write('...OK.\n')
        sys.stdout.flush()

        self.lines.sort(lambda x,y: cmp(x.resultline, y.resultline))

        # calculate age (0 - 100 where 100 is oldest and 0 is newest)
        oldest = None
        newest = None
        for commit in self.commits:
            if hasattr(commit, 'author_time'):
                if not oldest or oldest > commit.author_time:
                    oldest = commit.author_time
                if not newest or newest < commit.author_time:
                    newest = commit.author_time
        if oldest != newest:
            for commit in self.commits:
                if hasattr(commit, 'author_time'):
                    commit.age = 100 - int(100 * (commit.author_time - oldest)) / (newest - oldest)
                else:
                    commit.age = 100
        else:
            for commit in self.commits:
                commit.age = 100

def color_for_age(age):
    age = min(max(age, 0), 100)
    r = 255 - (age/3)
    g = 247 - (age/3)
    b = 241 - (age/3)
    return '#%02x%02x%02x'%(r,g,b)

class CommitTracker(object):
    def __init__(self):
        self.current_commit = None

class MainWindow(gtk.Window):
    def __init__(self):
        gtk.Window.__init__(self)
        self.connect('destroy', lambda w: gtk.main_quit())
        self.connect('delete_event', lambda w, event: gtk.main_quit())
        self.sourceview = None
        self.sourcebuffer = None
        self.langmanager = None
        self.stylemanager = None
        self.liststore = None
        self.image = None
        self.gravaloader = None
        self.tracker = None
        self.blamed = None
        self.sidelist = None

    def setup(self):
        self.sourcebuffer = gtksourceview2.Buffer()
        self.langmanager = gtksourceview2.LanguageManager()
        self.stylemanager = gtksourceview2.StyleSchemeManager()
        if 'tango' in self.stylemanager.get_scheme_ids():
            self.sourcebuffer.set_style_scheme(self.stylemanager.get_scheme('tango'))
        self.sourceview = gtksourceview2.View(self.sourcebuffer)
        self.sourceview.set_show_line_numbers(True)
        self.sourceview.modify_font(pango.FontDescription('Monospace'))

        sidesplit = gtk.HPaned()

        box = gtk.VBox()
        scroll = gtk.ScrolledWindow()
        scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        scroll.add(self.sourceview)
        box.pack_start(scroll, expand=True, fill=True, padding=0)
        self.liststore = gtk.ListStore(str, str)
        treeview = gtk.TreeView(self.liststore)
        treeview.set_headers_visible(False)
        col = gtk.TreeViewColumn(None, gtk.CellRendererText(), text=0)
        treeview.append_column(col)
        col = gtk.TreeViewColumn(None, gtk.CellRendererText(), text=1)
        treeview.append_column(col)
        scroll = gtk.ScrolledWindow()
        scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        scroll.add(treeview)
        scroll.set_property('height-request', 120)
        box2 = gtk.HBox()
        box2.pack_start(scroll, expand=True, fill=True, padding=0)
        gravaimg = gtk.Button()
        self.image = gtk.Image()
        self.image.set_size_request(80, 80)
        self.image.set_from_stock(gtk.STOCK_MISSING_IMAGE, gtk.ICON_SIZE_LARGE_TOOLBAR)
        self.image.show()
        gravaimg.add(self.image)
        self.gravaloader = GravatarLoader()
        self.gravaloader.start()

        box2.pack_end(gravaimg, expand=False, fill=True, padding=0)
        box.pack_end(box2, expand=False, fill=True, padding=4)

        sidesplit.pack1(box, resize=True)

        self.sidelist = gtk.ListStore(str)
        sidetree = gtk.TreeView(self.sidelist)
        #sidetree.set_headers_visible(False)
        col = gtk.TreeViewColumn("Authors", gtk.CellRendererText(), text=0)
        sidetree.append_column(col)
        sidetree.set_size_request(160, -1)

        sidesplit.pack2(sidetree, resize=False)

        sidesplit.set_position(-1)

        self.add(sidesplit)

    def do_blame(self, fil):
        language = self.langmanager.guess_language(fil)
        self.sourcebuffer.set_language(language)

        self.blamed = BlamedFile(fil, self.sourceview)
        if not self.blamed.lines:
            print "no lines to blame, sure this file is in a git repository?"
            sys.exit(1)

        if platform.system() == 'Windows':
            self.sourcebuffer.set_text(unicode(self.blamed.text,"iso-8859-1"))
        else:
            self.sourcebuffer.set_text(self.blamed.text)


        for age in range(101):
            # create marker type for age
            self.sourceview.set_mark_category_background('age%d'%(age), gtk.gdk.color_parse(color_for_age(age)))

        # TODO: do this for lines as they are loaded by loader thread
        for y in range(len(self.blamed.lines)):
            age = self.blamed.lines[y].commit.age
            line_start = self.sourcebuffer.get_iter_at_line(y)
            mark = self.sourcebuffer.create_source_mark(None, 'age%d'%(age), line_start)
            setattr(mark, 'blameline', self.blamed.lines[y])

        self.tracker = CommitTracker()


        self.sourcebuffer.connect_after('mark-set', self.on_mark_set, self.tracker)

        authdata = dict()
        for c in self.blamed.commits:
            if authdata.has_key(c.author):
                authdata[c.author][1] += 1
            else:
                authdata[c.author] = [0, 1]
        for line in self.blamed.lines:
            if line.commit:
                authdata[line.commit.author][0] += line.num_lines
        for a, llist in authdata.iteritems():
            self.sidelist.append(["%s (%d lines, %d commits)" % (a, llist[0], llist[1])])

    def pop_from_queue(self):
        self.gravaloader.sync_update()
        gots = self.gravaloader.query()
        if gots:
            self.image.set_from_file(gots)
            return False
        else:
            return True

    def on_mark_set(self, buffer, param, param2, tracker):
        iter = buffer.get_iter_at_mark(buffer.get_insert())
        marks = buffer.get_source_marks_at_line(iter.get_line(), None)
        if marks:
            for mark in marks:
                if hasattr(mark, 'blameline'):
                    blameline = getattr(mark, 'blameline')
                    commit = blameline.commit
                    if commit and tracker.current_commit is not commit:
                        self.liststore.clear()
                        self.liststore.append(['Author', commit.author])
                        self.liststore.append(['Email', commit.author_mail])
                        self.liststore.append(['Time', time.ctime(commit.author_time)])
                        self.liststore.append(['Summary', commit.summary])
                        if commit.sha1 != '0'*40:
                            self.liststore.append(['SHA1', commit.sha1])
                        #set image to
                        mail = commit.author_mail[1:-1]
                        if mail == "not.committed.yet":
                            self.image.set_from_stock(gtk.STOCK_DIALOG_WARNING, gtk.ICON_SIZE_LARGE_TOOLBAR)
                        else:
                            grava = self.gravaloader.query(commit.author_mail[1:-1])
                            if grava:
                                self.image.set_from_file(grava)
                            else:
                                gobject.idle_add(self.pop_from_queue)
                                self.image.set_from_stock(gtk.STOCK_MISSING_IMAGE, gtk.ICON_SIZE_LARGE_TOOLBAR)

                        tracker.current_commit = commit
                        return
        else:
            tracker.current_commit = None
            self.image.set_from_stock(gtk.STOCK_MISSING_IMAGE, gtk.ICON_SIZE_LARGE_TOOLBAR)
            self.liststore.clear()


def main(fil):
    win = MainWindow()

    from pkg_resources import resource_filename
    if platform.system() == 'Windows':
        iconfile = resource_filename(__name__, "data/peachy.ico")
    else:
        iconfile = resource_filename(__name__, "data/peachy.svg")

    if iconfile:
        try:
            win.set_icon_from_file(iconfile)
        except Exception, e:
            print e
    win.setup()

    win.do_blame(fil)

    win.resize(600,500)
    win.show_all()

    gtk.gdk.threads_init()
    gtk.main()

def usage():
    lic = """
    git-age  Copyright (C) 2008  Kristoffer Gronlund
    This program comes with ABSOLUTELY NO WARRANTY; for details see LICENSE.
    This is free software, and you are welcome to redistribute it
    under certain conditions; see LICENSE for details.
"""
    print "usage: %s <file>" % (sys.argv[0])
    print lic
    sys.exit(1)

if __name__=="__main__":
    if len(sys.argv) < 2: usage()
    main(sys.argv[1])
