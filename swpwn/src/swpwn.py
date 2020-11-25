#!/bin/python
import argparse
import os
import os.path
import docker
import sys
import json
import socket
import fcntl
import platform
import struct
import signal
import subprocess as sp

from distutils.dir_util import mkpath

APPNAME = 'swpwn'
APPAUTHOR = 'swpwn'

EXIST_FLAG = '/tmp/swpwn.id'
DAEMON_PID = '/tmp/swpwn.daemon.pid'

SUPPORTED_UBUNTU_VERSION = [
#    '14.04', Still many issues to be solved (version problems mostly)
    '16.04',
    '18.04',
    '18.10',
    '19.04',
    '20.04'
]

client = docker.from_env()
container = client.containers
image = client.images

class SetupError(Exception):
    pass

class InstallationError(Exception):
    pass

class AlreadyRuningException(Exception):
    pass

class NotRunningException(Exception):
    pass

class ColorWrite(object):
    COLOR_SET = {
            'END': '\033[0m',
            'yellow': '\033[38;5;226m',
            'red': '\033[31m',
            'blue': '\033[34m',
            'magenta': '\033[35m',
            'cyan': '\033[36m',
    }

    @staticmethod
    def color_write(content, color):
        print(ColorWrite.COLOR_SET[color] + content + ColorWrite.COLOR_SET['END'])

def colorwrite_init():
    for color in ColorWrite.COLOR_SET:
        # Use default value for lambda to avoid lazy capture of closure
        setattr(ColorWrite, color, staticmethod(lambda x, color=color: ColorWrite.color_write(x, color)))

# Static initialize ColorWrite
colorwrite_init()

def parse_args():
    """Parses commandline arguments
    Returns:
        args -- argparse namespace, contains the parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="swpwn's pwn environment"
    )
    subparsers = parser.add_subparsers(
        help='Actions you can take'
    )

    run_parser = subparsers.add_parser(
        'run',
        help='run a pwn thread'
    )
    run_parser.add_argument(
        'directory',
        type=str,
        help='The directory which contains your pwn challenge'
    )
    run_parser.add_argument(
        '--ubuntu',
        type=str,
        help='The version of ubuntu to open'
    )
    run_parser.add_argument(
        '--port',
        type=int,
        help='port of outside terminal server, default 15111'
    )
    run_parser.add_argument(
        '--priv',
        action='store_true',
        help='privileged boot, so you can use something like kvm'
    )
    run_parser.add_argument(
        '--name',
        type=str,
        default = None,
        help='Set the name of the container'
    )
    run_parser.set_defaults(func=run_pwn)

    attach_parser = subparsers.add_parser(
        'attach',
        help='attach to running thread',
    )
    attach_parser.set_defaults(func=attach_pwn)

    end_parser = subparsers.add_parser(
        'end',
        help='end a running thread'
    )
    end_parser.set_defaults(func=end_pwn)

    list_parser = subparsers.add_parser(
        'list',
        help='list all runing container'
    )
    list_parser.set_defaults(func=list_pwn)


    args = parser.parse_args()
    if vars(args) != {}:
        args.func(args)
    else:
        parser.print_usage()


def _get_terminal_size():
    p = sp.Popen('tput cols', shell=True, stdout=sp.PIPE)
    def _print_warning():
        print('Warning: Unable to get terminal size, you need to specify terminal size ' +
              'manually or your command line may behave strangely')
    if p.returncode != 0:
        _print_warning()
        return None, None
    cols = int(p.stdout)
    p = sp.Popen('tput lines', shell=True, stdout=sp.PIPE)
    if p.returncode != 0:
        _print_warning()
        return None, None
    rows = int(p.stdout)
    return cols, rows


def _read_container_name():
    if not os.path.exists(EXIST_FLAG):
        raise Exception('swpwn is not running, consider use swpwn run first')

    container_name = ''
    with open(EXIST_FLAG, 'r') as flag:
        container_name = flag.read()

    if container_name == '':
        os.remove(EXIST_FLAG)
        raise Exception('swpwn id file is  corrupted, or unable to read saved id file. ' + \
                'Cleaning corrupted id file, please shutdown the container manually')

    return container_name

def _attach_interactive(name):
    # cols, rows = _get_terminal_size()
    # if rows and cols:
    #     cmd = "docker exec -it {} bash -c \"{}\"".format(
    #         name,
    #         'stty cols {} && stty rows {} && bash'.format(
    #             cols,
    #             rows,
    #         )
    #     )
    # else:
    #     cmd = "docker exec -it {} '/bin/bash'".format(
    #         name,
    #     )
    cmd = "docker exec -it {} '/bin/bash'".format(
            name,
        )
    ColorWrite.yellow(
        r'''
__________               .__  .__  _____       
\______   \__  _  ______ |  | |__|/ ____\____  
 |     ___/\ \/ \/ /    \|  | |  \   __\/ __ \ 
 |    |     \     /   |  \  |_|  ||  | \  ___/ 
 |____|      \/\_/|___|  /____/__||__|  \___  >
                       \/                   \/ 
                                 no pwn no life 
'''
    )
    os.system(cmd)


def run_pwn(args):
    """Runs a pwn thread
    Just sets needed docker arguments and run it
    """
    # port = args.port if not args.port is None else 15111

    if not args.ubuntu:
        ubuntu = '16.04'
    else:
        # check for unsupported ubuntu version
        if args.ubuntu not in SUPPORTED_UBUNTU_VERSION:
            print('you are using ubuntu version %s' % args.ubuntu)
            print('this version may not be officially supported')
        ubuntu = args.ubuntu
    if not args.directory.startswith('~') and \
            not args.directory.startswith('/'):
                # relative path
        args.directory = os.path.abspath(args.directory)

    if not os.path.exists(args.directory):
        raise IOError('No such directory')

    if os.path.exists(EXIST_FLAG):
        raise AlreadyRuningException('swpwn is already running, you should either end it  to run again or attach it')

    # run server before dealing with docker
    # child_pid = os.fork()
    # if child_pid == 0:
    #     # sub process
    #     server = ServerProcess(DAEMON_PID, port, daemon=True)
    #     server.start()
    #     server.join() # hold it!
    #     return

    privileged = True if args.priv else False

    # First we need a running thread in the background, to hold existence
    try:
        if platform.system() != 'Darwin':
            os.system('xhost +')
            volumes = {
                os.path.expanduser(args.directory) : {
                    'bind': '/pwn',
                    'mode': 'rw'
                },
                os.path.expanduser('~/.Xauthority') : {
                    'bind': '/root/.Xauthority',
                    'mode': 'rw'
                },
                os.path.expanduser('/tmp/.X11-unix') : {
                    'bind': '/tmp/.X11-unix',
                    'mode': 'rw'
                }
            }
        else:
            volumes = {
                os.path.expanduser(args.directory) : {
                    'bind': '/pwn',
                    'mode': 'rw'
                }
            }
        running_container = container.run(
            'swpwn:{}'.format(ubuntu),
            '/bin/bash',
            cap_add=['SYS_ADMIN', 'SYS_PTRACE'],
            detach=True,
            tty=True,
            volumes=volumes,
            privileged=privileged,
            network_mode='host',
            name=args.name,
            #environment={
            #    'DISPLAY': os.environ['DISPLAY']
            #},
            remove=True, # This is important, or else we will have many stopped containers
        )
    except Exception as e:
        print('swpwn unable to run docker container')
        print('please refer to documentation to correctly setup your environment')
        print()
        raise e

    # Set flag, save the container id
    with open(EXIST_FLAG, 'w') as flag:
        flag.write(running_container.name)


    # Then attach to it, needs to do it in shell since we need
    # shell to do the input and output part(interactive part)
    _attach_interactive(running_container.name)
    

def attach_pwn(args):
    """Attaches to a pwn thread
    Just sets needed docker arguments and run it as well
    """
    container_name = _read_container_name()

    # FIXME Is it better that we just exec it with given name?
    conts = container.list(filters={'name':container_name})
    if len(conts) != 1:
        raise InstallationError('Installation seems to be run. There are more than one image called swpwn')
    _attach_interactive(conts[0].name)
    

def list_pwn(args):
    """List all running container
    """
    # containers_header = ['CONTAINER ID','IMAGE','STATUS','PORTS']
    # containers_data = []
    # for containers in container.list():
    #     print(containers.name)
    os.system('docker ps -a')


def end_pwn(args):
    """Ends a running thread
    """
    container_name = _read_container_name()
    conts = container.list(filters={'name':container_name})
    if len(conts) < 1:
        os.remove(EXIST_FLAG)
        raise NotRunningException('No pwn thread running, corrupted meta info file, deleted')
    conts[0].stop()
    os.remove(EXIST_FLAG)

    # with open(DAEMON_PID, 'r') as f:
    #     pid = int(f.read())

    # kill server daemon
    # os.kill(pid, signal.SIGTERM)
    # os.remove(DAEMON_PID)






def main():
    parse_args()


if __name__ == "__main__":
    main()
