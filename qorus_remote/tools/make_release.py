#!/usr/bin/env python3

import argparse
import datetime
import getpass
import glob
import os
import platform
import re
import shutil
import socket
import sys
import tarfile
import tempfile
import yaml
import stat
import time

import pkgutil


LoadFileTypes = {
    "qfd": True,
    "qsd": True,
    "java": True,
    "qclass": True,
    "qconst": True,
    "qwf": True,
    "qjob": True,
    "qconn": True,
    "qsm": True,
    "qmapper": True,
    "qvmap": True,
    "qscript": True,
    "qstep": True,
    "qmc": True,
    "yaml": True,
    "py": True
}

# file extensions to be ignored when packaging a release
ExtraFileTypes = {
    "wsdl": True,
    "xml": True,
    "xsd": True,
    "dtd": True,
    "qm": True,
    "qlib": True,
    "jar": True,
    "class": True,
    "json": True,
    "qtest": True,
    "qc": True,
    "qhtml": True,
    "qjs": True,
    "qjson": True,
}

def readonly_rmtree_handler(func, path, execinfo):
    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWUSR)  # or os.chmod(path, stat.S_IWRITE) from "stat" module
        func(path)
    else:
        raise

class MakeReleaseParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        print("""
        Examples:
        {}  -U. mylabel services/*.qsd
            It creates a user-code release with all service files
            in the default release-dir/mylabel directory.
        {}  -U. -lmylabel services/*.qsd
            It creates only the load script manifest for service files
            named mylabel.qrf
        \n""")
        sys.exit(1)

class MakeRelease:
    # main release dir
    release_dir: str = "make-release-{}-{}-pid-{}".format(socket.gethostname(), getpass.getuser(), os.getpid())

    # service resource types
    YamlServiceResources: tuple = [
        "resource",
        "text-resource",
        "bin-resource",
        "template",
    ]

    @staticmethod
    def getLabel(prefix: str, label: str) -> str:
        # check for file name instead of label
        if os.path.isfile(label):
            # print >> sys.stderr, "ERROR: label '{}' is an existing file - check arguments and try again".format(label)
            sys.stderr.write("ERROR: label '{}' is an existing file - check arguments and try again".format(label))
            sys.exit(1)

        if label[:len(prefix)] == prefix:
            return label

        return prefix + label

    @staticmethod
    def fixPrefix(string: str) -> str:
        # substitute repeated '/' with a single '/'
        string = re.sub('//+', '/', string)
        # remove leading '/'
        string = string[1:] if string[0] == '/' else string
        # remove trailing '/'
        string = string[:-1] if string[-1] == '/' else string

        return string

    @staticmethod
    def getPythonDestDir():
        return "user/python/lib/python{}.{}/site-packages".format(sys.version_info.major, sys.version_info.minor)

    @staticmethod
    def mkdir(d: str, msg: str = '', mode: int = 0o777) -> None:
        # do not try to create a directory that already exists
        if os.path.isdir(d):
            return
        try:
            os.makedirs(d, mode)
            print("mkdir " + d)
        except Exception as e:
            MakeRelease.error(d + ": mkdir({}) failed: " + repr(e))

    @staticmethod
    def doCmd(cmd: str):
        # cmd = '{} {}'.format(fmt, argv)
        # if self._opts.verbose:
        #     print("exec: {}".format(cmd))
        rc = os.system(cmd)
        if rc > 0:
            raise Exception("SYSTEM-ERROR", "command: {} returned error code {}".format(cmd, rc))

    @staticmethod
    def error(*args, **kwargs):
        nargs = list(args)
        nargs[0] = "ERROR: " + args[0]
        print(*nargs, file=sys.stderr, **kwargs)
        sys.exit(1)

    @staticmethod
    def is_readable(path):
        try:
            if os.path.isdir(path):
                return True
            else:
                with open(path) as f:
                    return f.readable()
        except IOError as err:
            return False

    def __init__(self):
        self._ulist: list = []
        self._rname: str = ''
        self._tar: str = ''

        # set release dir
        self._rdir = os.getenv('QORUS_RELEASE_DIR')
        if self._rdir is None:
            home_dir = os.getenv('HOME')
            if home_dir:
                self._rdir = os.path.join(home_dir, "releases")
        if self._rdir is None:
            self._rdir = os.path.join(os.getcwd(), "releases")
        # print(self._rdir)

        # set temp dir
        self._tmpdir = os.path.join(os.getcwd(), "temp")

        # get current working dir
        self._cwd = os.getcwd()

        # set options
        _parser = MakeReleaseParser()
        _parser.add_argument('-u', '--user', dest='user', help='add user code to system release')
        _parser.add_argument('-U', '--user-src', dest='usrc', help="""sets the root source directory for release
                            components; all release components should be given as
                            relative paths from this directory""")
        _parser.add_argument('-m', '--fix-module-paths', dest='mod', action='store_true', help='install Qore modules in user/modules')
        _parser.add_argument('-p', '--prefix', dest='pref', help="""sets user prefix directory for relative paths in the
                            target filesystem (implies -U.; note: makes a flat
                            release in this dir)""")
        _parser.add_argument('-P', '--add-prefix', dest='padd', help="""prepends a prefix dir for relative paths in the
                            target filesystem (implies -U.)""")
        _parser.add_argument('-r', '--release-dir', dest='rdir', help='set release directory (def: {})'.format(self._rdir))
        _parser.add_argument('-q', '--user-sql', dest='usql', default=[], help='adds an SQL file to execute in the omquser schema')
        _parser.add_argument('-R', '--show-release-dir', dest='showr', action='store_true', help="""show the release directory
                             and exit (def: {})""".format(self._rdir))
        _parser.add_argument('-c', '--compress', dest='comp', action='store_true', help='make a compressed tar file of the release')
        _parser.add_argument('-f', '--refresh', dest='ref', action='store_true', help='include commands to refresh objects after loading')
        _parser.add_argument('-C', '--refresh-compat', dest='rcompat', action='store_true', help="""include old commands to refresh objects after
                            loading""")
        _parser.add_argument('-F', '--full-release', dest='full', action='store_true', help='verify release completeness, only with -i')
        _parser.add_argument('-i', '--install', dest='inst', action='store_true', help='exec install.sh after packaging (user rel. only)')
        _parser.add_argument('--keep', dest='keep', action='store_true', help='do not delete temporary packaging directory')
        _parser.add_argument('-v', '--verbose', dest='verbose', default=False, action='store_true', help='output more information')
        _parser.add_argument('--usage', dest='usage', action='store_true', help='show this help text')
        _parser.add_argument('-a', '--python-modules', dest='pymoddir', help="""package the given directory as a Python module dir
                            to be installed in the target under
                            $OMQ_DIR/{}
                            (use -b to override the target dir)""".format(MakeRelease.getPythonDestDir()))
        _parser.add_argument('-b', '--python-dest', dest='pymoddest', help="""store files packaged with -a in the given directory
                            under $OMQ_DIR/""")
        _parser.add_argument('label')

        self._opts, self._args = _parser.parse_known_args()

        if self._opts.showr:
            print("{}".format(self._rdir))
            sys.exit(0)

        # set user label
        if self._opts.user:
            self._opts.user = MakeRelease.getLabel("qorus-user-", self._opts.user)

        if not self._opts.usrc and (self._opts.pref or self._opts.padd):
            self._opts.usrc = "."

        self._opts.usrc = os.path.abspath(self._opts.usrc)

        # fix prefix
        if self._opts.pref:
            self._opts.pref = self.fixPrefix(self._opts.pref)

        if self._opts.padd:
            self._opts.padd = self.fixPrefix(self._opts.padd)

        # if no user source listed, then set to current directory
        if not self._opts.usrc:
            self._opts.usrc = "."

        # set pymoddir/pymoddest
        if self._opts.pymoddir:
            if not self._opts.pymoddest:
                self._opts.pymoddest = MakeRelease.getPythonDestDir()
        elif self._opts.pymoddest:
            print("python-module-dest can only be used in addition to the python-module option")

    def checkAbsolutePath(self, path):
        if os.path.isabs(path):
            # print >> sys.stderr, "ERROR: release file component {} is an absolute path; must be a relative path from {}".format(path, self._opts.usrc)
            sys.stderr.write("ERROR: release file component {} is an absolute path; must be a relative path from {}".format(path, self._opts.usrc))
            sys.exit(1)

    def doFile2(self, file_list, d, path):
        self.checkAbsolutePath(path)

        # skip backup files
        if re.search('~$', path):
            return

        # skip release files
        if re.search('\\.qrf$', path):
            print('skipping release file \'{}\''.format(path))
            return

        if os.path.isdir(os.path.join(d, path)):
            # skip files in "old" directories
            if re.search('old$', path):
                return
            for path1 in glob.glob(os.path.join(os.path.join(d, path), "*")):
                self.doFile2(file_list, d, os.path.join(path, os.path.basename(path1)))
            return

        file_list.append(path)

    def doFile(self, path):
        self.checkAbsolutePath(path)

        # skip backup files
        if re.search('~$', path):
            return

        self.doFile2(self._ulist, self._opts.usrc, path)

        if not MakeRelease.is_readable(path):
            print("ERROR: cannot find file '{}'".format(path))
            sys.exit(1)

    def checkFiles(self, args):
        # make file lists
        self._ulist: list = []

        cwd = os.getcwd()
        os.chdir(self._opts.usrc)

        try:
            # and check to see if all files exist
            for path in args:
                if re.search('[*?]', path):
                    paths = glob.glob(path, recursive=True)
                    if not paths:
                        sys.stderr.write("ERROR: path {} does not match any files\n".format(path))
                        sys.stderr.flush()
                        sys.exit(1)
                    for path_item in paths:
                        self.doFile(path_item)
                    continue

                self.doFile(path)
        finally:
            os.chdir(cwd)

    def makeList(self, file_list: list, resource_list: list) -> list:
        # make a set of the file list for quick lookups
        file_map: dict = {i: True for i in file_list}

        madeList: list = []
        entry: str
        for entry in file_list:
            if re.search('~$', entry):
                continue

            if re.search('[*?]', entry):
                e: str
                for e in glob.glob(entry):
                    self.processFile(e, file_map, madeList, resource_list)
            else:
                self.processFile(entry, file_map, madeList, resource_list)

        return madeList

    def processResource(self, entry: str, resource_list: list, resource_name: str):
        self.checkAbsolutePath(resource_name)
        d: str = os.path.dirname(entry)
        resource_path: str = os.path.join(d, resource_name)

        if not re.search('[*?]', resource_name):
            if not MakeRelease.is_readable(resource_path):
                MakeRelease.error("service {} references resource {} that does not exist ({})".format(entry,
                    resource_name, re.error))
        elif not glob.glob(resource_path):
            MakeRelease.error("service {} references resource glob {} that does not match any files".format(
                entry, resource_name))
        resource_list.append({
            'source': resource_path,
            'target': resource_name,
        })

    def processFile(self, entry: str, file_map: dict, madeList: list, resource_list: list):
        madeList.append(entry)

        if re.search('\\.yaml$', entry):
            try:
                # try to parse the YAML and see if it has a code reference that can be added to the release
                with open(entry) as e:
                    fh = yaml.full_load(e)
                    if 'code' in fh:
                        src = fh['code']
                        src = os.path.join(os.path.dirname(entry), src)
                        if MakeRelease.is_readable(src) and src not in file_map:
                            file_map[src] = True
                            madeList.append(src)

                    am = None
                    if 'api-manager' in fh \
                        and 'provider-options' in fh['api-manager'] \
                        and 'schema' in fh['api-manager']['provider-options'] \
                        and 'value' in fh['api-manager']['provider-options']['schema']:
                        am = fh['api-manager']['provider-options']['schema']['value']

                    print('am: {}'.format(am))

                    # process resources from Qorus services
                    if re.search('\\.qsd\\.yaml$', entry):
                        resource_type: str
                        for resource_type in MakeRelease.YamlServiceResources:
                            resource_name: str
                            if fh.get(resource_type, None):
                                for resource_name in fh[resource_type]:
                                    self.processResource(entry, resource_list, resource_name)

                        # add API management resources
                        if 'api-manager' in fh \
                            and 'provider-options' in fh['api-manager'] \
                            and 'schema' in fh['api-manager']['provider-options'] \
                            and 'value' in fh['api-manager']['provider-options']['schema']:
                            self.processResource(entry, resource_list,
                                fh['api-manager']['provider-options']['schema']['value'])

            except Exception as e:
                print(e)
                pass

        # add service resources to released from old-style service files
        if resource_list and re.search('\\.qsd$', entry):
            with open(entry) as e:
                lines = e.readLines()

            for line in lines:
                resource_name = re.match('#[[:blank:]]*(resource|templates|bin-resource|text-resource)[[:blank:]]*:[[:blank:]]*(.+)/[1]', line)

                if resource_name:
                    resource_name = resource_name.strip()
                    self.processResource(entry, resource_list, resource_name)

    def delTree(self, targ):
        shutil.rmtree(targ, onerror=readonly_rmtree_handler)

    def deleteFolder(self, path):
        for obj in glob.glob(os.path.join(path, '*')):
            print('obj name is ', obj)
            if os.path.isdir(obj):
                self.deleteFolder(obj)
        print('removing path ', path)
        print(os.listdir(path))
        shutil.rmtree(path, False)
        time.sleep(1)

    def doCreateTar(self, opt: str, tarf: str, files: list, exclude: bool = False):
        with tarfile.open(tarf, 'w:{}'.format(opt)) as tar:
            for f in files:
                if exclude:
                    tar.add(f, filter=lambda tarinfo: None if re.search('~$', tarinfo.name) else tarinfo)
                else:
                    tar.add(f)
            tar.close()

    def doExtractTar(self, opt, tarf, dest='.'):
        with tarfile.open(tarf, 'r:{}'.format(opt)) as tar:
            tar.extractall(path=dest)

    def copyFiles(self, files, target):
        for f in files:
            if os.path.isdir(f):
                shutil.copytree(f, target)
            else:
                shutil.copy(f, target)

    def logDebug(self, *args, **kwargs):
        if self._opts.verbose:
            print(*args, **kwargs)

    def doResources(self, resource_list: list, dir_name: str):
        dh: dict = {}
        rh: dict
        for rh in resource_list:
            d: str = os.path.dirname(rh['source'])
            fn: str = os.path.basename(rh['source'])

            trdir: str = os.path.dirname(rh['target'])
            tdir: str = os.path.normpath(dir_name if trdir == '.' else os.path.join(dir_name, trdir))
            if tdir not in dh:
                if not os.path.isdir(tdir):
                    MakeRelease.mkdir(tdir)

                dh[tdir] = True

            # include all files in the subdirectory matching the pattern
            if re.search('[*?]', fn):
                dc: str = os.path.dirname(fn)
                if dc != ".":
                    fn = os.path.basename(fn)
                    d += os.path.sep + dc

                self.doGlob(d, fn, tdir)
            else:
                self.copyFiles([rh['source']], tdir + os.path.sep)

    def doGlob(self, d: str, fn: str, tdir: str):
        fstr: str
        for fstr in glob.glob(os.path.join(d, fn)):
            # skip files ending in "~" as backup files
            if re.search('~$', fstr):
                continue

            if os.path.isdir(fstr):
                self.doGlob(d, os.path.join(fstr[len(d):], os.path.basename(fn)), tdir)
                continue

            # skip special files, sockets, device files, etc
            if not os.path.isfile(fstr):
                continue

            targ: str = tdir + os.path.sep
            dir_name: str = os.path.dirname(fn)
            if dir_name != ".":
                targ += dir_name + os.path.sep
                if not os.path.isdir(targ):
                    MakeRelease.mkdir(targ)

            self.copyFiles([fstr], targ)

    def createUserReleaseFile(self, path: str, load_list: list = []):
        # create release file
        with open(path, 'w') as f:
            f.write("# automatically generated by {} on {} ({}@{})\n".format(os.path.basename(__file__),
                datetime.datetime.now(), os.getenv('USER'), socket.gethostname()))

            root_dir: str = os.path.dirname(path)
            if root_dir == ".":
                root_dir = ""

            fn: str
            if not load_list:
                load_list = self._ulist
            for fn in load_list:
                # check for known file extensions
                ext = MakeRelease.getExt(fn)
                if not ext:
                    # see if it's an executable
                    if not os.access(fn, os.X_OK):
                        print("warning: no extension in file '{}'...".format(fn))
                elif ext in LoadFileTypes:
                    load_file_path: str = self.getLoadPath(fn, root_dir)
                    if platform.system() == 'Windows':
                        load_file_path = load_file_path.replace('\\', '/')
                    # f.write("load {}\n".format(self.getLoadPath(fn, root_dir)))
                    f.write("load {}\n".format(load_file_path))
                elif ext not in ExtraFileTypes:
                    print("warning: unknown extension '{}' in file '{}'...".format(ext, fn))

            # now add user sql files
            for fn in self._opts.usql:
                # check for known file extensions
                ext = MakeRelease.getExt(fn)

                if not re.search('sql$', ext):
                   print("warning: user SQL file extension is not 'sql': {}".format(fn))

                f.write("omquser-exec-sql {}\n".format(self.getLoadPath(fn, root_dir)))

            if self._opts.ref:
                f.write("refresh-recursive\n")
            elif self._opts.rcompat:
                f.write("refresh-all\n")

        os.chmod(path, 0o644)
        print("created user release file {}".format(path))

    def getLoadPath(self, fn: str, root_dir: str) -> str:
        #print('getLoadPath() fn: {} root_dir: {} (pref: {} padd: {})'.format(fn, root_dir, self._opts.pref, self._opts.padd))

        if fn.startswith(root_dir):
            fn = fn[len(root_dir):]
        if self._opts.pref:
            return os.path.join(self._opts.pref, fn)
        if self._opts.padd:
            return os.path.join(self._opts.padd, fn)
        return fn

    def gettempdir(self):
        temp_dir = tempfile.gettempdir()
        if platform.system() == 'Windows':
            temp_dir = self._tmpdir
            if not os.path.isdir(temp_dir):
                MakeRelease.mkdir(temp_dir)
        return temp_dir

    @staticmethod
    def getExt(fn):
        m = re.match('.*\\.(.*)$', fn)
        return m.groups()[0] if m else ''

    def exec(self):
        if not self._args:
            print("ERROR: no files given on the command line")

        self.checkFiles(self._args)

        label = MakeRelease.getLabel("qorus-user-", self._opts.label)

        # check release directory
        if not os.path.isdir(self._rdir):
            MakeRelease.mkdir(self._rdir, "root release directory")

        rname = os.path.normpath(os.path.join(self._rdir, label))

        ulabel = label

        if not os.path.isdir(rname):
            MakeRelease.mkdir(rname, "release directory")

        if not os.path.isfile(os.path.join(rname, "install.sh")):
            # for packaging for PyPI, templates/install.sh has been moved inside the package of this module and is accessed as a resource using pkgutil.get_data
            # the original code is preserved as comments below
            _install_sh_pa = "templates/install.sh"
            _install_sh = pkgutil.get_data(__name__, _install_sh_pa)
            if not _install_sh: raise ValueError("_install_sh == " + repr(_install_sh))
            # pathname = os.path.join(os.path.join(os.path.dirname(os.path.realpath(__file__)), "templates"), "install.sh")

            # copy install.sh to new directory
            with open(os.path.join(rname, "install.sh"), "wb") as _install_sh_out:
                _install_sh_out.write(_install_sh)
            # self.copyFiles([pathname], os.path.join(rname, "install.sh"))

            print("copied <{}>{}{} to: {}".format(__package__, os.path.sep, _install_sh_pa, os.path.join(rname, "install.sh")))
            # print("copied:{} to:{}".format(pathname, os.path.join(rname, "install.sh")))

        if not os.path.isdir(os.path.join(rname, "releases")):
            # create releases subdirectory
            if MakeRelease.mkdir(os.path.join(rname, "releases")):
                MakeRelease.error("MakeRelease.mkdir({}) failed: {}".format(os.path.join(rname, "releases"), os.strerror(0)))

        if self._ulist:
            tar_file_name: str = "{}.tar.gz".format(ulabel)
            user_file_list: list = self._ulist
            if self._opts.usql:
                user_file_list += self._opts.usql

            # path -> info
            resource_list: list = []

            os.chdir(self._opts.usrc)

            file_list: list = self.makeList(user_file_list, resource_list)

            # get base dir for files
            base_dir: str = os.path.dirname(user_file_list[0])

            if not self._opts.pref and not self._opts.padd and resource_list:
                self._opts.padd = "user"

            load_list: list

            # create release in prefix directory
            to_delete = ''
            if self._opts.pref:
                try:
                    pdir = self._opts.pref

                    if platform.system() == 'Windows':
                        pdir = pdir.replace('/', '\\')

                    path_component_list: list = pdir.split(os.path.sep)

                    # get unique directory prefix name
                    unique_dir = MakeRelease.release_dir
                    dir_name: str = os.path.join(os.path.join(self.gettempdir(), unique_dir), pdir)

                    # create install directory
                    MakeRelease.mkdir(dir_name, mode=0o755)

                    to_delete = os.path.join(self.gettempdir(), unique_dir)

                    # copy files to temporary release directory
                    fn: str
                    for fn in file_list:
                        file_base_dir: str = os.path.dirname(fn)
                        targ_dir: str = dir_name
                        if base_dir != file_base_dir and file_base_dir[0:len(base_dir) - 1] == base_dir:
                            targ_dir = os.path.join(dir_name, file_base_dir[len(base_dir) + 1:]) + os.sep
                            MakeRelease.mkdir(targ_dir)
                        self.copyFiles([fn], targ_dir)

                    load_list = [os.path.basename(e) for e in user_file_list]
                    self.doResources(resource_list, dir_name)

                    # create tar file
                    os.chdir(os.path.join(self.gettempdir(), unique_dir))
                    self.doCreateTar("gz", os.path.join(rname, tar_file_name), [path_component_list[0]])
                    os.chdir(self._cwd)
                finally:
                    if not self._opts.keep and to_delete:
                        self.delTree(to_delete)
            elif self._opts.padd:
                try:
                    pdir = self._opts.padd

                    if platform.system() == 'Windows':
                        pdir = pdir.replace('/', '\\')

                    # get unique directory prefix name
                    unique_dir = MakeRelease.release_dir

                    # get list of path components
                    path_component_list = pdir.split(os.path.sep)

                    # create install directory
                    dir_name: str = os.path.join(os.path.join(self.gettempdir(), unique_dir), pdir)
                    MakeRelease.mkdir(dir_name)

                    self.logDebug('creating install dir: {}'.format(dir_name))

                    to_delete = os.path.join(self.gettempdir(), unique_dir)
                    # to_delete = dir_name

                    load_list = [pdir + '/' + os.path.basename(e) for e in user_file_list]
                    self.doResources(resource_list, dir_name)

                    # copy files to temporary release directory
                    # create temporary tar file
                    tmp_tar = os.path.join(self.gettempdir(), os.path.join(unique_dir, "tqr.tar.gz"))
                    self.doCreateTar("gz", tmp_tar, file_list)

                    # unpack release in new position
                    os.chdir(dir_name)
                    self.doExtractTar("gz", tmp_tar)

                    # move module files to new location
                    if (self._opts.mod):
                        target_dir = ''
                        done: dict = {}
                        for fn in self._ulist:
                            if re.search('\\.qm$', fn):
                                # do not move module files in a directory with the same name
                                bn: str = os.path.basename(fn)
                                if (os.path.basename(os.path.dirname(fn)) + ".qm") == bn:
                                    continue
                                if bn in done:
                                    continue

                                if not target_dir:
                                    target_dir = os.path.join(self.gettempdir(), os.path.join(unique_dir, "user/modules"))
                                    MakeRelease.mkdir(target_dir, mode=0o755)

                                # move module file to target
                                targ = os.path.join(target_dir, os.path.basename(fn))
                                os.rename(fn, targ)
                                done[bn] = True

                    # create tar file
                    os.chdir(os.path.join(self.gettempdir(), unique_dir))
                    self.doCreateTar("gz", os.path.join(rname, tar_file_name), [path_component_list[0]])
                    os.chdir(self._cwd)
                finally:
                    if not self._opts.keep and to_delete:
                        self.delTree(to_delete)
                        # self.deleteFolder(to_delete)
            else:
                self.doCreateTar("gz", os.path.join(rname, tar_file_name), file_list)
                load_list = user_file_list

            print("created user tar file {}/{}".format(rname, tar_file_name))

            if self._opts.pymoddir:
                os.chdir(rname)
                temp = MakeRelease.release_dir
                MakeRelease.mkdir(temp)

                self.doExtractTar("gz", tar_file_name, temp)
                dest = os.path.join(rname, os.path.join(temp, self._opts.pymoddest))
                MakeRelease.mkdir(dest)

                self.copyFiles([self._opts.pymoddir], os.path.join(dest, os.path.basename(self._opts.pymoddir)))
                os.chdir(temp)

                self.doCreateTar("gz", os.path.join(rname, tar_file_name), glob.glob('*'))
                self.delTree(os.path.join(rname, temp))
                print("Adding python module at destination: {}".format(self._opts.pymoddest))

            # create release file
            release_file_namef = "{}/releases/{}.qrf".format(rname, ulabel)
            self.createUserReleaseFile(release_file_namef, load_list)

            if self._opts.comp:
                # save current working directory
                print('rname: {} ut: {}'.format(rname, tar_file_name))
                os.chdir(os.path.dirname(rname))
                # make compressed release tar.bz2; exclude backup files
                tar = label + ".tar.bz2"
                self.doCreateTar("bz2", tar, [label], exclude=True)
                print("create release archive: {}/{}".format(os.getcwd(), tar))

            if self._opts.inst:
                os.chdir(rname)

                opts = ""
                if self._opts.verbose:
                    opts += " -v"
                if self._opts.full:
                    opts += " -F"

                MakeRelease.doCmd("sh ./install.sh" + opts)

            os.chdir(self._cwd)
            print("done!")


# globals = {}
def main():
    makeRelease = MakeRelease()
    makeRelease.exec()


if __name__ == "__main__":
    main()
