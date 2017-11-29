#!/usr/bin/env python

# couchdb_upgrade.py
# https://github.com/pietervogelaar/couchdb_upgrade
#
# Performs a rolling upgrade of a CouchDB cluster
#
# Installing dependencies:
# pip install requests
#
# MIT License
#
# Copyright (c) 2017 Pieter Vogelaar
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import datetime
import json
import requests
import subprocess
import sys
import time
from distutils.version import StrictVersion
from requests.auth import HTTPBasicAuth
from requests.exceptions import ConnectionError


class CouchDbUpgrader:
    """
    Performs a rolling upgrade of a CouchDB cluster
    """

    def __init__(self,
                 nodes,
                 username=None,
                 password=None,
                 port=5984,
                 ssl=False,
                 service_stop_command='sudo systemctl stop couchdb',
                 service_start_command='sudo systemctl start couchdb',
                 upgrade_command='sudo yum clean all && sudo yum install -y couchdb',
                 latest_version_command="sudo yum clean all >/dev/null 2>&1 && yum list all couchdb |"
                                        " grep couchdb | awk '{ print $2 }' | cut -d '-' -f1 |"
                                        " sort --version-sort -r | head -n 1",
                 check_stable_command="stable=$(grep 'publish cluster `stable` event' /var/log/couchdb/couchdb.log |"
                                      " while read -r line; do timestamp=$(echo $line | awk '{ print $2 }'); if ["
                                      " \"$(date -d\"$timestamp\" +'%Y%m%d%H%M%S')\" -ge \"{service_start_time}\" ];"
                                      " then echo 'yes'; fi; done); if [ \"$stable\" != \"yes\" ]; then exit 1; fi",
                 version='latest',
                 reboot=False,
                 force_reboot=False,
                 verbose=False,
                 ):
        """
        Constructor
        :param nodes: list Host names or IP addresses of nodes
        :param username: string
        :param password: string
        :param port: int
        :param ssl: bool
        :param service_stop_command: string
        :param service_start_command: string
        :param upgrade_command: string
        :param latest_version_command: string
        :param check_stable_command: string
        :param version: string
        :param reboot: bool
        :param force_reboot: bool
        :param verbose: bool
        """

        self._nodes = nodes
        self._username = username
        self._password = password
        self._port = port
        self._ssl = ssl
        self._service_stop_command = service_stop_command
        self._service_start_command = service_start_command
        self._upgrade_command = upgrade_command
        self._latest_version_command = latest_version_command
        self._check_stable_command = check_stable_command
        self._version = version
        self._reboot = reboot
        self._force_reboot = force_reboot
        self._verbose = verbose
        self._service_start_time = None

    def verbose_response(self, response):
        if self._verbose:
            print('Response status code: {}'.format(response.status_code))
            print('Response headers: {}'.format(response.headers))
            print('Response content: {}'.format(response.text))

    def current_version_lower(self, node):
        """
        Checks if the current version of CouchDB on the node
        is lower than the version to upgrade to
        :param node: string
        :return: bool
        """
        response = requests.get(self.get_node_url(node))
        self.verbose_response(response)

        if response.status_code == 200:
            data = response.json()
            if 'version' in data:
                if StrictVersion(data['version']) == StrictVersion(self._version):
                    print('Skipping, the current version {} is the same as the version to upgrade to'
                          .format(data['version']))
                    return False
                elif StrictVersion(data['version']) > StrictVersion(self._version):
                    print('Skipping, the current version {} is higher than version {} to upgrade to'
                          .format(data['version'], self._version))
                    return False
                else:
                    print('The current version {} is lower than version {} to upgrade to'
                          .format(data['version'], self._version))
                    return True
            else:
                sys.stderr.write("Could not determine the current version\n")
        else:
            sys.stderr.write("Could not retrieve the current version\n")

        return False

    def stop_service(self, node):
        """
        Stops the CouchDB service on the node
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._service_stop_command)
        if result['exit_code'] != 0:
            return False

        return True

    def upgrade_couchdb(self, node):
        """
        Upgrades the CouchDB software on the node
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._upgrade_command)

        if self._verbose:
            print('stdout:')
            print(result['stdout'])
            print('stderr:')
            print(result['stderr'])

        if result['exit_code'] != 0:
            return False

        if self._force_reboot:
            self.reboot(node)
        elif self._reboot and 'Nothing to do' not in result['stdout']:
            self.reboot(node)

        return True

    def start_service(self, node):
        """
        Starts the CouchDB service on the node
        :param node: string
        :return: bool
        """

        self._service_start_time = datetime.datetime.now()

        result = self.ssh_command(node, self._service_start_command)
        if result['exit_code'] != 0:
            return False

        return True

    def wait_until_joined(self, node):
        """
        Waits until the node joined the cluster
        :param node:
        :return: bool
        """

        print('- Waiting until node joins the cluster')

        while True:
            time.sleep(5)

            url = '{}/_membership'.format(self.get_node_url(node))

            try:
                if self._username:
                    auth = HTTPBasicAuth(self._username, self._password)
                else:
                    auth = None

                response = requests.get(url, auth=auth)
                self.verbose_response(response)

                if response.status_code == 200:
                    data = response.json()

                    if ('all_nodes' in data and
                        any(node in s for s in data['all_nodes']) and
                        'cluster_nodes' in data and
                        any(node in s for s in data['cluster_nodes'])):

                        if self._verbose:
                            print("Node joined the cluster")
                        else:
                            sys.stdout.write(".\n")
                            sys.stdout.flush()

                        return True
            except ConnectionError as exception:
                if self._verbose:
                    print('Could not connect to node')

            if self._verbose:
                print("Node hasn't joined the cluster yet")
            else:
                sys.stdout.write('.')
                sys.stdout.flush()

    def wait_until_status_stable(self, node):
        """
        Waits until the cluster status is stable
        :param node:
        :return: bool
        """

        print('- Waiting until cluster status is stable')

        while True:
            time.sleep(5)

            service_start_time_string = self._service_start_time.strftime('%Y%m%d%H%M%S')
            command = self._check_stable_command.replace('{service_start_time}', service_start_time_string)

            result = self.ssh_command(node, command)

            if result['exit_code'] == 0:
                if self._verbose:
                    print('Cluster status is stable')
                else:
                    sys.stdout.write(".\n")
                    sys.stdout.flush()

                return True

            if self._verbose:
                print('Cluster status is not stable yet')
            else:
                sys.stdout.write('.')
                sys.stdout.flush()

    def get_latest_version(self, node):
        """
        Gets the latest version available in the repository
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._latest_version_command)
        if result['exit_code'] != 0:
            return False

        latest_version = result['stdout'].strip()
        if StrictVersion(latest_version) > StrictVersion('0.0.0'):
            return latest_version

        return False

    def reboot(self, node):
        print('- Rebooting')
        self.ssh_command(node, 'sudo /sbin/reboot')

    def get_node_url(self, node):
        """
        Gets a node URL
        :param node: string
        :return: string
        """
        if self._ssl:
            protocol = 'https'
        else:
            protocol = 'http'

        return '{}://{}:{}'.format(protocol, node, self._port)

    def ssh_command(self, host, command):
        """
        Executes a SSH command
        :param host: string
        :param command: string
        :return: dict
        """
        p = subprocess.Popen(['ssh', '%s' % host, command],
                             shell=False,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)

        stdout = p.stdout.readlines()
        stderr = p.stderr.readlines()

        if stderr:
            sys.stderr.write("SSH error from host {}: {}\n".format(host, ''.join(stderr)))

        # Make a return code available
        p.communicate()[0]

        result = {
            'stdout': ''.join(stdout),
            'stderr': ''.join(stderr),
            'exit_code': p.returncode,
        }

        return result

    def upgrade_node(self, node):
        print('Node {}'.format(node))

        self._service_start_time = datetime.datetime.now()
        rebooting = False

        if self._version:
            # Only upgrade node if the current version is lower than the version to upgrade to
            if not self.current_version_lower(node):
                if self._force_reboot:
                    rebooting = True
                    self.reboot(node)
                else:
                    return True

        if not rebooting:
            # Stop CouchDB service
            print('- Stopping CouchDB service')
            if not self.stop_service(node):
                sys.stderr.write("Failed to stop CouchDB service\n")
                return False

            # Upgrade the CouchDB software
            print('- Upgrading CouchDB software')
            if not self.upgrade_couchdb(node):
                sys.stderr.write("Failed to upgrade CouchDB software\n")
                return False

            # Start CouchDB service
            print('- Starting CouchDB service')
            if not self.start_service(node):
                sys.stderr.write("Failed to start CouchDB service\n")
                return False

        self.wait_until_joined(node)
        self.wait_until_status_stable(node)

        return True

    def upgrade(self):
        print('Performing a rolling upgrade of the CouchDB cluster')

        if self._verbose:
            print('Cluster nodes: {}'.format(json.dumps(self._nodes)))

        if self._version == 'latest':
            print('Determining the latest version')

            latest_version = self.get_latest_version(self._nodes[0])
            if latest_version:
                print('Using latest version {} as version to upgrade to'.format(latest_version))
                self._version = latest_version
            else:
                sys.stderr.write("Failed to determine the latest version\n")
                return False

        for node in self._nodes:
            if not self.upgrade_node(node):
                sys.stderr.write("Failed to patch the CouchDB cluster\n")
                return False

        print ('Successfully upgraded all nodes of the CouchDB cluster')

        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Performs a rolling upgrade of a CouchDB cluster')
    parser.add_argument('-n', '--nodes', help='Comma separated list of host names or IP addresses of nodes',
                        required=True)
    parser.add_argument('-u', '--username', help="Username for authentication")
    parser.add_argument('-P', '--password', help="Password for authentication")
    parser.add_argument('-p', '--port', help='CouchDB HTTP port. Default 5984', type=int, default=5984)
    parser.add_argument('-s', '--ssl', help='Connect with https', action='store_true')
    parser.add_argument('--service-stop-command',
                        help="Shell command to stop the CouchDB service on a node. "
                             "Default 'sudo systemctl stop couchdb'",
                        default='sudo systemctl stop couchdb')
    parser.add_argument('--service-start-command',
                        help="Shell command to start the CouchDB service on a node. "
                             "Default 'sudo systemctl start couchdb'",
                        default='sudo systemctl start couchdb')
    parser.add_argument('--upgrade-command',
                        help="Command to upgrade CouchDB on a node. "
                             "Default 'sudo yum clean all && sudo yum install -y couchdb'",
                        default='sudo yum clean all && sudo yum install -y couchdb')
    parser.add_argument('--latest-version-command',
                        help="Command to get the latest version in the repository. "
                             "Default \"sudo yum clean all >/dev/null 2>&1 && sudo yum list all couchdb |"
                             " grep couchdb | awk '{ print $2 }' | cut -d '-' -f1 | sort --version-sort -r |"
                             " head -n 1\"",
                        default="sudo yum clean all >/dev/null 2>&1 && sudo yum list all couchdb |"
                                " grep couchdb | awk '{ print $2 }' | cut -d '-' -f1 | sort --version-sort -r |"
                                " head -n 1")
    parser.add_argument('--check-stable-command',
                        help="Command to check if the cluster status is stable again after a node that"
                             " rejoined the cluster. Default \"stable=$(grep 'publish cluster `stable` event'"
                             " /var/log/couchdb/couchdb.log | while read -r line; do timestamp=$(echo $line |"
                             " awk '{ print $2 }'); if [ \"$(date -d\"$timestamp\" +'%%Y%%m%%d%%H%%M%%S')\" -ge"
                             " \"{service_start_time}\" ]; then echo 'yes'; fi; done); if [ \"$stable\" != \"yes\" ];"
                             " then exit 1; fi\"",
                        default="stable=$(grep 'publish cluster `stable` event' /var/log/couchdb/couchdb.log |"
                                " while read -r line; do timestamp=$(echo $line | awk '{ print $2 }'); if ["
                                " \"$(date -d\"$timestamp\" +'%Y%m%d%H%M%S')\" -ge \"{service_start_time}\" ];"
                                " then echo 'yes'; fi; done); if [ \"$stable\" != \"yes\" ]; then exit 1; fi")
    parser.add_argument('--version',
                        help="A specific version to upgrade to or 'latest'. If 'latest', then the highest"
                             " available version in the repository will be determined. Nodes with a version"
                             " equal or higher will be skipped. Default 'latest'",
                        default='latest')
    parser.add_argument('--reboot', help='Reboots the server if an actual upgrade took place', action='store_true')
    parser.add_argument('--force-reboot', help='Always reboots the server, even though no upgrade occurred because'
                                               ' the version was already the latest', action='store_true')
    parser.add_argument('-v', '--verbose', help='Display of more information', action='store_true')
    args = parser.parse_args()

    # Create nodes list from comma separated string
    nodes = args.nodes.replace(' ', '').split(',')

    couchdb_upgrader = CouchDbUpgrader(nodes,
                                       args.username,
                                       args.password,
                                       args.port,
                                       args.ssl,
                                       args.service_stop_command,
                                       args.service_start_command,
                                       args.upgrade_command,
                                       args.latest_version_command,
                                       args.check_stable_command,
                                       args.version,
                                       args.reboot,
                                       args.force_reboot,
                                       args.verbose)

    if not couchdb_upgrader.upgrade():
        exit(1)
