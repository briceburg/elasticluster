#! /usr/bin/env python
#
# Copyright (C) 2013 GC3, University of Zurich
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
__author__ = 'Nicolas Baer <nicolas.baer@uzh.ch>'

# stdlib imports
from abc import ABCMeta, abstractmethod
from fnmatch import fnmatch
import os
import sys

# local imports
from elasticluster.conf import Configurator
from elasticluster.conf import Configuration
from elasticluster import log
from elasticluster.exceptions import ClusterNotFound, ConfigurationError
from elasticluster.exceptions import ImageError, SecurityGroupError
from elasticluster.exceptions import NodeNotFound


class AbstractCommand():
    """
    Defines the general contract every command has to fullfill in
    order to be recognized by the arguments list and executed
    afterwards.
    """
    __metaclass__ = ABCMeta

    def __init__(self, params):
        """
        A reference to the parameters of the command line will be
        passed here to adjust the functionality of the command
        properly.
        """
        self.params = params

    @abstractmethod
    def setup(self, subparsers):
        """
        This method handles the setup of the subcommand. In order to
        do so, every command has to add a parser to the subparsers
        reference given as parameter. The following example is the
        minimum implementation of such a setup procedure: parser =
        subparsers.add_parser("start")
        parser.set_defaults(func=self.execute)
        """
        pass

    @abstractmethod
    def execute(self):
        """
        This method is executed after a command was recognized and may
        vary in its behavior.
        """
        pass

    def __call__(self):
        return self.execute()

    def pre_run(self):
        """
        Overrides this method to execute any pre-run code, especially
        to check any command line options.
        """
        pass


def cluster_summary(cluster):
    try:
        frontend = cluster.get_frontend_node().name
    except NodeNotFound, ex:
        frontend = 'unknown'
        log.error("Unable to get information on the frontend node: "
                  "%s", str(ex))
    msg = """
Cluster name:     %s
Cluster template: %s
Frontend node: %s
""" % (cluster.name, cluster.template, frontend)

    for cls in cluster.nodes:
        msg += "- %s nodes: %d\n" % (cls, len(cluster.nodes[cls]))
    msg += """
To login on the frontend node, run the command:

    elasticluster ssh %s

To upload or download files to the cluster, use the command:

    elasticluster sftp %s
""" % (cluster.name, cluster.name )
    return msg

class Start(AbstractCommand):
    """
    Create a new cluster using the given cluster template.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "start", help="Create a cluster using the supplied configuration.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster',
                            help="Type of cluster. It refers to a "
                            "configuration stanza [cluster/<name>]")
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('-n', '--name', dest='cluster_name',
                            help='Name of the cluster.')
        parser.add_argument('--nodes', metavar='N1:GROUP[,N2:GROUP2,...]', 
                            help='Override the values in of the configuration file and starts `N1` nodes of group `GROUP`, N2 of GROUP2 etc...')
        parser.add_argument('--no-setup', action="store_true", default=False,
                            help="Only start the cluster, do not configure it")

    def pre_run(self):
        self.params.extra_conf = {}
        try:
            if self.params.nodes:
                nodes = self.params.nodes.split(',')
                for nspec in nodes:
                    n, group = nspec.split(':')
                    n = int(n)
                    self.params.extra_conf[group+'_nodes'] = n
        except ValueError:
            raise ConfigurationError(
                "Invalid argument for option --nodes: %s" % self.params.nodes)

    def execute(self):
        """
        Starts a new cluster.
        """

        cluster_template = self.params.cluster
        if self.params.cluster_name:
            cluster_name = self.params.cluster_name
        else:
            cluster_name = self.params.cluster

        # First, check if the cluster is already created.
        try:
            cluster = Configurator().load_cluster(cluster_name)
        except ClusterNotFound, ex:
            if self.params.cluster_name:
                self.params.extra_conf['name'] = self.params.cluster_name

            try:
                cluster = Configurator().create_cluster(
                    cluster_template, **self.params.extra_conf)
            except ConfigurationError, ex:
                log.error("Starting cluster %s: %s\n",
                          cluster_template, ex)
                return

        try:
            for cls in cluster.nodes:
                print("Starting cluster `%s` with %d %s nodes." % (
                cluster.name, len(cluster.nodes[cls]), cls))
            print("(this may take a while...)")
            cluster.start()
            if self.params.no_setup:
                print("NOT configuring the cluster as requested.")
            else:
                print("Configuring the cluster.")
                print("(this too may take a while...)")
                ret = cluster.setup()
                if ret:
                    print("Your cluster is ready!")
            print(cluster_summary(cluster))
        except (KeyError, ImageError, SecurityGroupError) as e:
            print("Your cluster could not start `%s`" % e)


class Stop(AbstractCommand):
    """
    Stop a cluster and terminate all associated virtual machines.
    """
    def setup(self, subparsers):
        """
        @see abstract_command contract
        """
        parser = subparsers.add_parser(
            "stop", help="Stop a cluster and all associated VM instances.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--force', action="store_true", default=False,
                            help="Remove the cluster even if not all the nodes"
                            " have been terminated properly.")
        parser.add_argument('--yes', action="store_true", default=False,
                            help="Assume `yes` to all queries and do not prompt.")

    def execute(self):
        """
        Stops the cluster if it's running.
        """
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Stopping cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        if not self.params.yes:
            yesno = raw_input("Do you want really want to stop cluster %s? [yN] " % cluster_name)
            if yesno.lower() not in ['yes', 'y']:
                print("Aborting as per user request.")
                sys.exit(0)
        print("Destroying cluster `%s`" % cluster_name)
        cluster.stop(force=self.params.force)


class ResizeCluster(AbstractCommand):
    """
    Resize the cluster by adding or removing compute nodes.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "resize", help="Resize a cluster by adding or removing "
            "compute nodes.", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('--nodes', metavar='+-N1:GROUP1[,+-N2:GROUP2]',
                            help="Add/remove N1 nodes of group GROUP1, N2 of group GROUP2 etc...")
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--no-setup', action="store_true", default=False,
                            help="Only start the cluster, do not configure it")

    def pre_run(self):
        self.params.nodes_to_add = {}
        self.params.nodes_to_remove = {}
        try:
            if self.params.nodes:
                nodes = self.params.nodes.split(',')
                for nspec in nodes:
                    n, group = nspec.split(':')
                    if n[0] == '-':
                        self.params.nodes_to_remove[group] = int(n[1:])
                    else:
                        self.params.nodes_to_add[group] = int(n)
        except ValueError:
            raise ConfigurationError(
                "Invalid syntax for argument: %s" % self.params.nodes)

    def execute(self):
        # Get current cluster configuration
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Listing nodes from cluster %s: %s\n" %
                      (cluster_name, ex))
            return
        for grp in self.params.nodes_to_add:
            print("Adding %d %s node(s) to the cluster"
                  "" % (self.params.nodes_to_add[grp], grp))
            for i in range(self.params.nodes_to_add[grp]):
                cluster.add_node(grp)

        for grp in self.params.nodes_to_remove:
            print("Removing %d %s node(s) from the cluster."
                  "" % (self.params.nodes_to_remove[grp], grp))
            for i in range(self.params.nodes_to_remove[grp]):
                node = cluster.nodes[grp].pop()
                node.stop()

        cluster.start()
        if self.params.no_setup:
            print("NOT configuring the cluster as requested.")
        else:
            print("Reconfiguring the cluster.")
            cluster.setup()
        print(cluster_summary(cluster))


class ListClusters(AbstractCommand):
    """
    Print a list of all clusters that have been started.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list", help="List all started clusters.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")

    def execute(self):
        storage = Configurator().create_cluster_storage()
        cluster_names = storage.get_stored_clusters()

        if not cluster_names:
            print("No clusters found.")
        else:
            print("""
The following clusters have been started.
Please note that there's no guarantee that they are fully configured:
""")
            for name in sorted(cluster_names):
                try:
                    cluster = Configurator().load_cluster(name)
                except ConfigurationError, ex:
                    log.error("gettin information from cluster `%s`: %s",
                              name, ex)
                    continue
                print("%s " % name)
                print("-"*len(name))
                print("  name:           %s" % cluster.name)
                print("  template:       %s" % cluster.template)
                print("  cloud:          %s " % cluster._cloud)
                for cls in cluster.nodes:
                    print("  - %s nodes: %d" % (cls, len(cluster.nodes[cls])))
                print("")


class ListTemplates(AbstractCommand):
    """
    List the available templates defined in the configuration file.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list-templates", help="Show the templates defined in the "
            "configuration file.", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('clusters', help="List only this cluster. "
                            "Accepts globbing.", nargs="*")

    def execute(self):
        templates = Configuration.Instance().list_cluster_templates()
        for pattern in self.params.clusters:
            templates = [t for t in templates if fnmatch(t, pattern)]
        print("""%d cluster templates found.""" % len(templates))
        for template in templates:
            try:
                cluster = Configurator().create_cluster(template)
                print("""
name:     %s
image id: %s
flavor:   %s
cloud:    %s""" % (template, cluster.extra['image_id'],
                   cluster.extra['flavor'],
                   cluster._cloud))
                for nodetype in cluster.nodes:
                    print("%s nodes: %d" % (
                            nodetype,
                            len(cluster.nodes[nodetype])))
            except ConfigurationError, ex:
                log.warning("unable to load cluster `%s`: %s", template, ex)

class ListNodes(AbstractCommand):
    """
    Show some information on all the nodes belonging to a given
    cluster.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list-nodes", help="Show information about the nodes in the "
            "cluster", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument(
            '-u', '--update', action='store_true', default=False,
            help="By default `elasticluster list-nodes` will not contact the "
            "EC2 provider to get up-to-date information, unless `-u` option "
            "is given.")

    def execute(self):
        """
        Lists all nodes within the specified cluster with certain
        information like id and ip.
        """
        Configuration.Instance().cluster_name = self.params.cluster
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
            if self.params.update:
                cluster.update()
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Listing nodes from cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        print(cluster_summary(cluster))
        for cls in cluster.nodes:
            print("%s nodes:" % cls)
            print("")
            for node in cluster.nodes[cls]:
                txt = ["    " + i for i in node.pprint().splitlines()]
                print('  - ' + str.join("\n", txt)[4:])
                print("")


class SetupCluster(AbstractCommand):
    """
    Setup the given cluster by calling the setup provider defined for
    this cluster.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "setup", help="Configure the cluster.", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")

    def execute(self):
        Configuration.Instance().cluster_name = self.params.cluster
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        print("Configuring cluster `%s`..." % cluster_name)
        cluster.setup()
        print("Your cluster is ready!")
        print(cluster_summary(cluster))


class SshFrontend(AbstractCommand):
    """
    Connect to the frontend of the cluster using `ssh`.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "ssh", help="Connect to the frontend of the cluster using the "
            "`ssh` command", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('ssh_args', metavar='args', nargs='*',
                            help="Execute the following command on the remote "
                            "machine instead of opening an interactive shell.")

    def execute(self):
        Configuration.Instance().cluster_name = self.params.cluster
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        try:
            frontend = cluster.get_frontend_node()
        except NodeNotFound, ex:
            log.error("Unable to connect to the frontend node: %s" % str(ex))
            sys.exit(1)
        host = frontend.ip_public
        username = frontend.image_user
        ssh_cmdline = [
            "ssh",
            "-i", frontend.user_key_private,
        ] + (['-v'] * self.params.verbose) + [
            '%s@%s' % (username, host),
        ] + self.params.ssh_args
        os.execlp("ssh", *ssh_cmdline)


class SftpFrontend(AbstractCommand):
    """
    Open an SFTP session to the cluster frontend host.
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "sftp",
            help="Open an SFTP session to the cluster frontend host.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('sftp_args', metavar='args', nargs='*',
                            help="Arguments to pass to ftp, instead of "
                            "opening an interactive shell.")

    def execute(self):
        Configuration.Instance().cluster_name = self.params.cluster
        cluster_name = self.params.cluster
        try:
            cluster = Configurator().load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError), ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        try:
            frontend = cluster.get_frontend_node()
        except NodeNotFound, ex:
            log.error("Unable to connect to the frontend node: %s" % str(ex))
            sys.exit(1)
        host = frontend.ip_public
        username = frontend.image_user
        sftp_cmdline = [
            "sftp",
            "-i", frontend.user_key_private,
        ] + (['-v'] * self.params.verbose) + self.params.sftp_args + [
            '%s@%s' % (username, host),
        ]
        os.execlp("sftp", *sftp_cmdline)
