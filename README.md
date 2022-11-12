# Overview

This package contains two scripts:
- `qorus-remote-commands`: allows for command-line access to a remote Qorus server
- `make-release`: allows for releases to be created from local files

# Remote Qorus Commands
Requires Qorus 5.0.4+ and Python 3

The `qorus-remote-commands` script allows the user to run Qorus and Qore commands on a remote Qorus server using the
Qorus HTTP server and receive the output of the executed command in real time via the WebSocket protocol.

This allows the Qorus client to be used on any system with python 3 and to access Qorus running in a container or in
any other type of deployment as long as the HTTP server is accessible.

**NOTE**: the `oload` command is mean to be used with local files; local files are copied to the server to a temporary
location, and then `oload` is executed on the server with the deployed files.  At the end of the remote `oload`
execution, the temporary files are deleted,

## Installation

Install via pip:

`pip install qorus-remote`

## Usage
`qorus-remote-commands [-h|--help|--usage] <NETRC-FILE> <COMMAND> [<COMMAND-ARGS> ...]`

## Concrete Usage Examples

`qorus-remote-commands ~/.netrc-qorus-local qctl ps`

`qorus-remote-commands ~/.netrc-qorus-local qctl threads qorus-core`

`qorus-remote-commands ~/.netrc-qorus-local qrest system/starttime`

## .netrc file
| Variable | Description | Mandatory |
| --- | --- | --- |
| `machine` | ip address of the Qorus server machine | Yes |
| `port` | port of the Qorus server machine | Yes |
| `secure` | `yes` if the Qorus server is on `https`, no otherwise | Yes |
| `login` | Qorus username | Yes |
| `password` | Qorus password | Yes |
| `timeout` | Maximum time in seconds allowed for each of the curl operation | No |
| `verbose` | Makes the script verbose | No |
| `nodelete` | Does not delete the upload folder on the server | No |

### Example .netrc file
For a Qorus server located on https://localhost:8011 and using the Qorus user `adm` (`.netrc-qorus-local`):
```
machine localhost
port 8011
secure yes
login adm
password adm
timeout 120
verbose no
```

## Commands

### Qorus commands
`oload`\
`ocmd`\
`ojview`\
`oprop`\
`ostart`\
`ostatus`\
`ostop`\
`oview`\
`make-release`\
`qctl`\
`qrest`\
`schema-tool`\
`user-tool`

### Qore commands
`rest`\
`schema-reverse`\
`sfrest`\
`soaputil`\
`sqlutil`\
`qdp`\
`saprest`

### Aliases

It's recommended to create aliases for each of the above commands like:
- Unix/Linux: `alias oload='qorus-remote-commands ~/.netrc-qorus-local oload $*'`
- Windows: `DOSKEY oload=qorus-remote-commands %USERPROFILE%\Qorus\netrc-qorus-local oload $*`

etc

# make-release

The `make-release` script allows the user to make Qorus releases that can be manually or automatically deployed to
Qorus servers.

## Examples
`make-release -U. mylabel services/*.qsd`

Creates a user-code release with all service files in the default release-dir/mylabel directory.

`make-release -U. -lmylabel services/*.qsd`

Ceates only the load script manifest for service files in a release named mylabel.qrf
