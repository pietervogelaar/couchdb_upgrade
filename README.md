# couchdb_upgrade

Performs a rolling upgrade of a CouchDB cluster. It's great for keeping your cluster automatically
patched without downtime.

Nodes that already have the correct version are skipped. So the script can be executed multiple times if desired. 

# Usage

    usage: couchdb_upgrade.py [-h] -n NODES [-u USERNAME] [-P PASSWORD] [-p PORT]
                              [-s] [--service-stop-command SERVICE_STOP_COMMAND]
                              [--service-start-command SERVICE_START_COMMAND]
                              [--upgrade-command UPGRADE_COMMAND]
                              [--latest-version-command LATEST_VERSION_COMMAND]
                              [--check-stable-command CHECK_STABLE_COMMAND]
                              [--version VERSION]
                              [--upgrade-system-command UPGRADE_SYSTEM_COMMAND]
                              [--upgrade-system] [--reboot] [--force-reboot] [-v]
    
    Performs a rolling upgrade of a CouchDB cluster
    
    optional arguments:
      -h, --help            show this help message and exit
      -n NODES, --nodes NODES
                            Comma separated list of host names or IP addresses of
                            nodes
      -u USERNAME, --username USERNAME
                            Username for authentication
      -P PASSWORD, --password PASSWORD
                            Password for authentication
      -p PORT, --port PORT  CouchDB HTTP port. Default 5984
      -s, --ssl             Connect with https
      --service-stop-command SERVICE_STOP_COMMAND
                            Shell command to stop the CouchDB service on a node.
                            Default 'sudo systemctl stop couchdb'
      --service-start-command SERVICE_START_COMMAND
                            Shell command to start the CouchDB service on a node.
                            Default 'sudo systemctl start couchdb'
      --upgrade-command UPGRADE_COMMAND
                            Command to upgrade CouchDB on a node. Default 'sudo
                            yum clean all && sudo yum install -y couchdb'
      --latest-version-command LATEST_VERSION_COMMAND
                            Command to get the latest version in the repository.
                            Default "sudo yum clean all >/dev/null 2>&1 && sudo
                            yum list all couchdb | grep couchdb | awk '{ print $2
                            }' | cut -d '-' -f1 | sort --version-sort -r | head -n
                            1"
      --check-stable-command CHECK_STABLE_COMMAND
                            Command to check if the cluster status is stable again
                            after a node that rejoined the cluster. Default
                            "stable=$(grep 'publish cluster `stable` event'
                            /var/log/couchdb/couchdb.log | while read -r line; do
                            timestamp=$(echo $line | awk '{ print $2 }'); if [
                            "$(date -d"$timestamp" +'%Y%m%d%H%M%S')" -ge
                            "{service_start_time}" ]; then echo 'yes'; fi; done);
                            if [ "$stable" != "yes" ]; then exit 1; fi"
      --version VERSION     A specific version to upgrade to or 'latest'. If
                            'latest', then the highest available version in the
                            repository will be determined. Nodes with a version
                            equal or higher will be skipped. Default 'latest'
      --upgrade-system-command UPGRADE_SYSTEM_COMMAND
                            Command to upgrade operating system. Default 'sudo yum
                            clean all && sudo yum update -y'
      --upgrade-system      Upgrades the operating system also after upgrading
                            CouchDB
      --reboot              Reboots the server if an actual upgrade took place
      --force-reboot        Always reboots the server, even though no upgrade
                            occurred because the version was already the latest
      -v, --verbose         Display of more information

Only the nodes parameter is required. This script works by default with a YUM installation
of CouchDB. But with the command parameters it can be configured for other operating
systems as well. It should also work with archive (tar) based installations.

**As root user**:

    ./couchdb_upgrade.py --nodes host1,host2,host3
                
**As non-root user with restrictive sudo rights**:

    ./couchdb_upgrade.py\
     --nodes host1,host2,host3\
     --service-stop-command 'sudo /usr/local/bin/couchdbctl service stop couchdb'\
     --service-start-command 'sudo /usr/local/bin/couchdbctl service start couchdb'\
     --upgrade-command 'sudo /usr/local/bin/couchdbctl update'\
     --latest-version-command 'sudo /usr/local/bin/couchdbctl latest-version'

# Restrictive sudo rights

The upgrade script requires several actions that must be executed as root. But it would be
better to let a non-root user execute the upgrade script with restrictive sudo rights. A nice way
to do that is with sudo line and script below. 

**/etc/sudoers.d/couchdbctl**

    # Allow myuser to use couchdbctl that can stop/start/restart the couchdb service
    myuser ALL=(root) NOPASSWD: /usr/local/bin/couchdbctl

**/usr/local/bin/couchdbctl**

    #!/bin/bash
    
    # CouchDB ctl
    # This file exists to perform limited actions with sudo
    
    if [ "$1" == "service" ]; then
      if [ "$2" != 'start' ] && [ "$2" != 'stop' ] && [ "$2" != 'restart' ]; then
        echo 'Service sub command must be start, stop or restart'
        exit 1
      fi
    
      # Check if service name is empty
      if [[ -z "$3" ]]; then
        echo 'Service name must be specified'
        exit 1
      fi
    
      # Check if service name starts with "couchdb"
      if [[ "$3" != "couchdb"* ]]; then
        echo 'Service name must start with couchdb'
        exit 1
      fi
    
      systemctl $2 $3
    elif [ "$1" == "latest-version" ]; then
      sudo yum clean all >/dev/null 2>&1 &&
      yum list all couchdb | grep couchdb | awk '{ print $2 }' | cut -d '-' -f1 |
      sort --version-sort -r | head -n 1
    elif [ "$1" == "update" ]; then
      sudo yum clean all && sudo yum install -y couchdb
    elif [[ ! -z "$1" ]] ; then
      echo 'This sub command is not allowed'
      exit 1
    else
      echo 'Usage:'
      echo "./couchdbctl service (start|stop|restart) couchdb"
      echo "./couchdbctl latest-version"
      echo "./couchdbctl update"
    fi

# Disable SSH strict host key checking

If you have a trusted environment, you can disable strict host key checking to avoid having to type "yes"
for a SSH connection to each node. However, keep in mind that this could be a security risk.

Add to the ~/.ssh/config file of the user how executes this script:

    StrictHostKeyChecking no
    UserKnownHostsFile=/dev/null
    LogLevel ERROR
