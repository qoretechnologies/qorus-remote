#!/usr/bin/env python3

import asyncio
import datetime
import getopt
import os
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import socket
import ssl
import sys
import traceback
import websocket
import yaml
import glob
import warnings

warnings.filterwarnings(action="ignore", message="unclosed", category=ResourceWarning)

# server messages:
QCM_TextOutput = 'text-output'

def remote_print(string):
    if globals.get('verbose', '') == "yes":
        print(string)

def remote_print_exception(e):
    if globals.get('verbose', '') == "yes":
        tb_lines = traceback.format_exception(e.__class__, e, e.__traceback__)
        tb_text = ''.join(tb_lines)
        remote_print(tb_text)
    else:
        print(e)

    sys.exit(1)

def print_args():
    remote_print("Will execute: {}".format(globals['cmd']['cmd']))
    remote_print("Arguments: {}".format(" ".join(globals['cmd']['args'])))


def remote_print_usage(code):
    print("""usage: ./qorus-remote-commands.py [-h|--help|--usage] <NETRC-FILE> <COMMAND> [<COMMAND-ARGS> ...]
Examples:
    ./qorus-remote-commands.py example.netrc ostatus -S
    ./qorus-remote-commands.py example.netrc oload qorus-user-building-blocks-1.0-2020-11-12.tar.bz2""")
    sys.exit(code)


def extract_netrc(netrc_file):
    if not os.path.exists(netrc_file):
        print("netrc configuration file \"{}\" does not exist".format(netrc_file))
        return False

    try:
        with open(netrc_file,'r') as f:
            for line in f:
                if 'machine ' in line:
                    machine = line.replace('machine ', '').rstrip()
                elif 'port ' in line:
                    port = line.replace('port ', '').rstrip()
                elif 'secure ' in line:
                    secure = line.replace('secure ', '').rstrip()
                elif 'login ' in line:
                    globals['login'] = line.replace('login ', '').rstrip()
                elif 'password ' in line:
                    globals['password'] = line.replace('password ', '').rstrip()
                elif 'timeout ' in line:
                    globals['timeout'] = line.replace('timeout ', '').rstrip()
                elif 'verbose ' in line:
                    globals['verbose'] = line.replace('verbose ', '').rstrip()
                elif 'nodelete ' in line:
                    globals['nodelete'] = line.replace('nodelete ', '').rstrip()

        if not machine and not port and not secure:
            print("Impossible to find the netrc configuration in this file: \"{}\"".format(netrc_file))
            return False
        elif not machine:
            print("\"machine\" field is not defined in the netrc configuration file \"{}\"".format(netrc_file))
            return False
        elif not port:
            print("\"port\" field is not defined in the netrc configuration file \"{}\"".format(netrc_file))
            return False
        elif not secure:
            print("\"secure\" field is not defined in the netrc configuration file \"{}\"".format(netrc_file))
            return False
        elif 'login' not in globals:
            print("\"login\" field is not defined in the netrc configuration file \"{}\"".format(netrc_file))
            return False
        elif 'password' not in globals:
            print("\"password\" field is not defined in the netrc configuration file \"{}\"".format(netrc_file))
            return False

        if secure and secure == "yes":
            globals['URL'] = "wss://{}:{}/".format(machine, port)
        else:
            globals['URL'] = "ws://{}:{}/".format(machine, port)

        return True
    except Exception as e:
        print('Error while extracting URL from .netrc file')
        remote_print_exception(e)


def parse_args(args):
    opts_h = ['-h', '--help', '--usage']

    try:
        for o in opts_h:
            if o in args[:2 if len(args) >= 2 else 1]:
                remote_print_usage(0)
        if not args[0] or args[0] == "-h" or args[0] == "--help" or args[0] == "--usage":
            remote_print_usage(1)

        netrc_file = args[0]
        if not extract_netrc(netrc_file):
            sys.exit(1)

        if len(args) < 2:
            remote_print_usage(0)

        cmd = {}
        cmd['cmd'] = args[1]
        cmd['args'] = args[2:]

        return cmd
    except Exception as e:
        print('Error while parsing args')
        remote_print_exception(e)

def on_message(ws, message):
    try:
        msg = yaml.safe_load(message)
        if msg['msgtype'] == QCM_TextOutput:
            print(msg['data'], end='', flush=True)
        else:
            print('unknown command from server:', msg['msgtype'])
    except Exception as e:
        print('Error processing websocket message')
        remote_print_exception(e)

def on_error(ws, error):
    print("Websocket error:", error)

def on_close(ws, close_status_code, close_msg):
    pass

def on_open(ws):
    remote_print(yaml.dump(globals['cmd']))
    ws.send(yaml.dump(globals['cmd']))

def exec_cmd():
    try:
        connection = "{}remote-command".format(globals['URL'])
        url = '{}api/latest/system/wstoken?err=1'.format(globals['URL'].replace('wss', 'https').replace('ws', 'http'))
        token = requests.get(url, auth=requests.auth.HTTPBasicAuth(globals['login'], globals['password']), verify=False)
        # issue #2: check status code of token response
        if (token.status_code / 100) != 2:
            if token.status_code == 409 and 'application/json' in token.headers['content-type']:
                body = token.json()
                loc = body['file']
                line = body['line'] + body['offset']
                err = body['err']
                desc = body['desc']
                raise RuntimeError(f'Qorus server error at {loc}:{line}: {err}: {desc}')
            raise IOError(f'Error status code {token.status_code}: {token.text}')
        remote_print('Qorus-Token: {}'.format(token.text.strip('\"')))
        ws = websocket.WebSocketApp(connection,
            on_message = on_message,
            on_open = on_open,
            on_error = on_error,
            on_close = on_close,
            header = { 'Qorus-Token': token.text.strip('\"')})
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
    except Exception as e:
        print('Exception executing command')
        remote_print_exception(e)

# oload functions
def oload_check_option_with_spaced_arg(opt):
    possibilities = {'-' : ['p', 'r', 's', 't', 'u', 'D', 'L', 'X'],
                     '--': ['schema', 'user-schema', 'url', 'proxy-url', 'data-ts', 'index-ts', 'delete',
                            'delete-id', 'datasource', 'list', 'refresh', 'token', 'export-cfg-val', 'show-release']
    }

    if opt[:2] in possibilities:
        return opt[2:] in possibilities[opt[:2]]
    elif opt[:1] in possibilities:
        return opt[1:] in possibilities[opt[:1]]

    return False


def oloadparse_args(args):
    ofiles = []
    oopts = []

    try:
        coming_arg = False
        for arg in args:
            if coming_arg:
                oopts.append(arg)
                coming_arg = False
            elif arg[0] == '-':
                oopts.append(arg)
                if oload_check_option_with_spaced_arg(arg):
                    coming_arg = True
            else:
                ofiles.append(arg)

        return ofiles, oopts
    except Exception as e:
        print('Exception when parsing the oload arguments')
        remote_print_exception(e)


def oload_remove_dir(ofiles):
    try:
        for ofile in ofiles:
            if os.path.isdir(ofile):
                ofiles.remove(ofile)
    except Exception as e:
        print('Exception when removing directories from oload arguments')
        remote_print_exception(e)


def oload_remove_dir_from_files(ofiles):
    files_no_dir = []
    try:
        for ofile in ofiles:
            files_no_dir.append(os.path.basename(ofile))
    except Exception as e:
        print('Exception when removing directories from oload files')
        remote_print_exception(e)
    return files_no_dir


def oload_remove_files(ofiles):
    try:
        for ofile in ofiles:
            if not os.path.exists(ofile):
                ofiles.remove(ofile)
                print('File does not exist: {}'.format(ofile))
    except Exception as e:
        print('Exception when removing non-existing files from oload arguments')
        remote_print_exception(e)


def oload_add_src_files(ofiles: set, filemap: dict) -> list:
    try:
        sfiles: list = []
        ofile: str
        for ofile in ofiles:
            done: bool = False
            if ofile[-5:] == '.yaml' or ofile[-4:] == '.yml':
                with open(ofile) as of:
                    doc = yaml.full_load(of)
                    if doc.get('code'):
                        path: str = os.path.join(os.path.dirname(ofile), doc.get('code'))
                        sfiles.append(path)
                        filemap[path] = doc.get('code')
                        done = True

            if not done:
                filemap[ofile] = os.path.basename(ofile)

        return sfiles
    except Exception as e:
        print('Exception when adding source file for yaml files')
        remote_print_exception(e)

# recursively process resource files/directories
def oload_process_resource_path(rfiles: list, path: str, root: str, filemap: dict):
    if '*' in path:
        g: str
        for g in glob.glob(path):
            oload_process_resource_path(rfiles, g, root, filemap)
    else:
        if os.path.isdir(path):
            oload_process_resource_path(rfiles, path + '/*', root, filemap)
        else:
            rfiles.append(path)
            filemap[path] = os.path.relpath(path, root)

def oload_add_resource_files(ofiles: set, filemap: dict) -> list:
    try:
        rfiles: list = []
        ofile: str
        for ofile in ofiles:
            if ofile[-5:] == '.yaml' or ofile[-4:] == '.yml':
                with open(ofile) as of:
                    doc = yaml.full_load(of)
                    for r in doc.get('resource', []):
                        root: str = os.path.dirname(ofile)
                        path = os.path.join(root, r)
                        oload_process_resource_path(rfiles, path, root, filemap)
                    # add API management resources
                    if 'api-manager' in doc \
                        and 'provider-options' in doc['api-manager'] \
                        and 'schema' in doc['api-manager']['provider-options'] \
                        and 'value' in doc['api-manager']['provider-options']['schema']:
                        schema_file: str = doc['api-manager']['provider-options']['schema']['value']
                        path = os.path.join(os.path.dirname(ofile), schema_file)
                        rfiles.append(path)
                        filemap[path] = schema_file

        return rfiles
    except Exception as e:
        print('Exception when checking files for resource')
        remote_print_exception(e)

def oload_add_qrf_files(ofiles):
    try:
        qfiles = []
        for ofile in ofiles:
            if ofile[-4:] == '.qrf':
                with open(ofile) as of:
                    for line in of:
                        if 'load ' in line:
                            l = line.replace('load ', '').rstrip()
                            qfiles.append(os.path.join(os.path.dirname(ofile), l))
        return qfiles
    except Exception as e:
        print('Exception when checking qrf files')
        remote_print_exception(e)

# ofiles: original file list to process
# filemap: map of file names to relative target names
def oload_add_files(ofiles: set, filemap: dict) -> set:
    todo: set = ofiles.copy()
    toUpload: set = ofiles

    tmp = set()

    remote_print('Checking for yaml files and add their source files if they are missing')
    tmp.update(oload_add_src_files(todo, filemap))

    remote_print('Checking service files for resource')
    tmp.update(oload_add_resource_files(todo, filemap))

    remote_print('Checking qrf files for files to load')
    tmp.update(oload_add_qrf_files(todo))

    toUpload.update(tmp)

    return toUpload


def oload_upload_files(files: set, filemap: dict, directory: str = '') -> str:
    if not files:
        return directory

    try:
        url: str = '{}raw/remote-file'.format(globals['URL'].replace('wss', 'https').replace('ws', 'http'))

        print('Uploading files to remote host \"{}\": '.format(globals['URL']), end='')
        if globals.get('verbose', '') == "yes":
            print()

        ofile: str
        for ofile in files:
            filename: str = filemap.get(ofile, os.path.basename(ofile))

            if not directory:
                d: str = os.path.dirname(ofile)
                headers: dict = {'Content-Type': 'application/octet-stream', 'filepath': filename}
                with open(ofile,'rb') as data:
                    res = requests.post(url, data=data, headers=headers, verify=False,
                        auth=requests.auth.HTTPBasicAuth(globals['login'], globals['password']))
                    if '<html><head><title>' in res.text:
                        print(res.text)
                        sys.exit(1)
                    else:
                        directory = res.text
                        remote_print('\nUploading into {} directory'.format(directory))
            else:
                headers: dict = {'Content-Type': 'application/octet-stream', 'filepath': filename, 'dir': directory}
                with open(ofile,'rb') as data:
                    res = requests.post(url, data=data, headers=headers, verify=False,
                        auth=requests.auth.HTTPBasicAuth(globals['login'], globals['password']))
                    if '<html><head><title>' in res.text:
                        print(res.text)
                        sys.exit(1)

            if globals.get('verbose', '') == "yes":
                remote_print('Uploaded {} -> {}'.format(ofile, filename))
            else:
                print('.', end='', flush=True)

        if not globals.get('verbose', '') == "yes":
            print()
        return directory
    except Exception as e:
        print('\nException when uploading files')
        remote_print_exception(e)


def deleting_directory(directory):
    if not (globals.get('nodelete', '') == 'yes'):
        try:
            remote_print('Deleting folder: {}'.format(directory))
            url = '{}raw/remote-file'.format(globals['URL'].replace('wss', 'https').replace('ws', 'http'))
            headers = { 'dir': directory }
            remote_print('Sending curl request at {}\n{}'.format(url, headers))
            res = requests.delete(url, headers=headers, verify=False)
        except Exception as e:
            print('Exception when deleting remote directory')
            remote_print_exception(e)


def oload_handle(args):
    remote_print('Parsing args for options and loaded files')
    ofiles, oopts = oloadparse_args(args)

    remote_print('Removing directories from the file list')
    oload_remove_dir(ofiles)

    remote_print('Removing non-existing files from the file list')
    oload_remove_files(ofiles)

    # filemap: map of local paths to relative target paths
    filemap: dict = dict()
    toUpload: set = oload_add_files(set(ofiles), filemap)
    directory: str = oload_upload_files(toUpload, filemap)

    if globals['cmd']['cmd'] == 'oload':
        ofiles = oload_remove_dir_from_files(ofiles)

    remote_print('Executing \'oload {} "{}"\' on remote host'.format(oopts, " ".join(ofiles)))
    globals['cmd']['args'] = []
    globals['cmd']['files'] = ofiles
    globals['cmd']['opts'] = oopts
    globals['cmd']['dir'] = directory
    exec_cmd()

    deleting_directory(directory)


globals = {}
def main():
    if not sys.argv[1:]:
        remote_print_usage(1)

    globals['cmd'] = parse_args(sys.argv[1:])
    print_args()

    if globals['cmd']['cmd'] == 'oload':
        oload_handle(globals['cmd']['args'])
    else:
        exec_cmd()


if __name__ == "__main__":
    main()
