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

import ConfigParser
import os
import sys

from elasticluster import log
from elasticluster.providers.ec2_boto import BotoCloudProvider
from elasticluster.providers.gce import GoogleCloudProvider
from elasticluster.providers.ansible_provider import AnsibleSetupProvider
from elasticluster.helpers import Singleton
from elasticluster.cluster import Node, ClusterStorage
from elasticluster.exceptions import ConfigurationError, ClusterNotFound
from elasticluster.cluster import Cluster


class Configurator(object):
    """
    Responsible to create instances, which need information from the
    configuration file.
    """

    cloud_providers_map = {
        "ec2_boto": BotoCloudProvider,
        "google":   GoogleCloudProvider,
    }

    setup_providers_map = {"ansible": AnsibleSetupProvider, }

    def create_cloud_provider(self, cloud_name):
        """
        Creates a new cloud provider with the needed information from
        the configuration.
        """
        config = Configuration.Instance().read_cloud_section(cloud_name)

        if 'provider' not in config:
            raise ConfigurationError(
                "Missing `provider` configuration option in configuration "
                "file.")

        provider = Configurator.cloud_providers_map[config["provider"]]

        if config['provider'] == 'ec2_boto':
            args = dict()
            # required parameters. They may be found also in the
            # program environment.
            for param in ['ec2_url', 'ec2_region',
                          'ec2_access_key', 'ec2_secret_key']:
                PARAM = param.upper()
                if PARAM in os.environ:
                    args[param] = os.environ[PARAM]
                elif param in config:
                    args[param] = config[param]
                else:
                    raise ConfigurationError(
                        "Required configuration parameter '%s' missing from "
                        "configuration file section 'cloud/%s' and "
                        "environment variable '%s' is not set."
                        % (param, cloud_name, PARAM))
            return provider(**args)
        elif config['provider'] == 'google':
            # required parameters
            for param in ['client_id', 'client_secret', 'project_id']:
                if param not in config:
                    raise ConfigurationError(
                        "Required parameter '%s' missing from "
                        "configuration section 'cloud/%s'"
                        % (param, cloud_name))
                else:
                    args[param] = config[param]
            # add optional parameters
            for param in ['zone', 'network', 'email']:
                if param in config:
                    args[param] = config[param]
            # create the provider
            return provider(**args)

        else:  # Invalid `provider`
            raise ConfigurationError(
                "Invalid value `%s` for cloud `provider` in configuration "
                "file." % config['provider'])

    def create_cluster(self, cluster_template, **extra_args):
        """
        Creates a cluster with the needed information from the
        configuration.
        """
        try:
            config = Configuration.Instance().read_cluster_section(
                cluster_template)
        except ConfigParser.NoSectionError, ex:
            raise ConfigurationError(
                "Invalid configuration for cluster `%s`: %s"
                "" % (cluster_template, str(ex)))
        config['name'] = cluster_template

        # Update with extra conf
        config.update(extra_args)

        for key in ['cloud', 'setup_provider']:
            if key not in config:
                raise ConfigurationError(
                    "Invalid configuration for cluster `%s`: "
                    "missing configuration key `%s`." % (config['name'], key))

        nodes = dict((k[:-6],int(config[k])) for k in config if k.endswith('_nodes'))

        return Cluster(cluster_template,
                       config.pop('name'),
                       config['cloud'],
                       self.create_cloud_provider(config.pop('cloud')),
                       self.create_setup_provider(
                           config.pop("setup_provider"), cluster_template),
                       nodes,
                       self,  # ANTONIO: why self? Why at the end? It
                       ssh_to=config.pop('ssh_to', None),
                       **config)
                              # does not looks right

    def load_cluster(self, cluster_name):
        storage = self.create_cluster_storage()
        information = storage.load_cluster(cluster_name)

        cluster = Configurator().create_cluster(
            information['template'], name=information['name'])

        # Clear cluster nodes.
        cluster.nodes = dict((k, []) for k in cluster.nodes)
        for dnode in information['nodes']:
            if dnode['type'] not in cluster.nodes:
                cluster.nodes[dnode['type']] = []
            node = cluster.add_node(dnode['type'], name=dnode['name'])
            node.instance_id = dnode['instance_id']
            node.ip_public = dnode['ip_public']
            node.ip_private = dnode['ip_private']

        return cluster

    def create_node(self, cluster_name, node_type, cloud_provider, name):
        """
        Creates a node with the needed information from the
        configuration file. The information of the node is specific to
        its type (e.g. a frontend node could differ from a compute
        node).
        """
        config = Configuration.Instance().read_node_section(
            cluster_name, node_type)

        for key in ['user_key_private', 'user_key_name', 'image_user',
                    'security_group', 'image_id', 'flavor']:
            if key not in config:
                raise ConfigurationError(
                    "Invalid configuration for node `%s` in cluster `%s`: "
                    "missing configuration key `%s`." % (
                        name, cluster_name, key))

        return Node(name, node_type, cloud_provider, config['user_key_public'],
                    config["user_key_private"], config['user_key_name'],
                    config['image_user'], config['security_group'],
                    config['image_id'], config['flavor'],
                    image_userdata=config.get('image_userdata', ''))

    def create_cluster_storage(self):
        """
        Creates the storage to manage clusters.
        """
        return ClusterStorage(Configuration.Instance().storage_path)

    def create_setup_provider(self, setup_provider_name, cluster_name):
        config = Configuration.Instance().read_setup_section(
            setup_provider_name, cluster_name)

        if config.get('provider') not in Configurator.setup_providers_map:
            raise ConfigurationError(
                "Invalid value `%s` for `setup_provider` in configuration "
                "file." % config.get('provider'))

        provider = Configurator.setup_providers_map[config['provider']]

        return provider(
            config.pop('user_key_private'), config.pop('image_user'),
            config.pop('image_user_sudo'), config.pop('image_sudo'),
            config.pop('playbook_path'), **config)


class QuotelessConfigParser(ConfigParser.ConfigParser):
    """
    This implementation removes all the quotes from the value of a property.
    """
    def get(self, section, option):
        val = ConfigParser.ConfigParser.get(self, section, option)
        return val.strip('"').strip("'")

    def items(self, section):
        items = ConfigParser.ConfigParser.items(self, section)
        items_stripped = []
        for i in items:
            l = list(i)
            if l[1]:
                l[1] = l[1].strip("'").strip('"')
            items_stripped.append(tuple(l))

        return items_stripped


@Singleton
class Configuration(object):
    """
    Singleton

    The configuration class handles the global configuration file.  It
    parses the file and provides the important sections as *datatype
    undecied*
    """

    mandatory_cloud_options = ("provider", "ec2_url", "ec2_access_key",
                               "ec2_secret_key", "ec2_region")
    mandatory_cluster_options = ("cloud", "frontend", "compute",
                                 "setup_provider", "login")
    mandatory_node_options = ("image_id", "security_group", "image_userdata",
                              "flavor")
    mandatory_setup_options = ("provider", "playbook_path", "frontend_groups",
                               "compute_groups")
    mandatory_login_options = ("image_user", "image_user_sudo", "image_sudo",
                               "user_key_name", "user_key_private",
                               "user_key_public")

    config_defaults = {
        'ansible_pb_dir': os.path.join(
            sys.prefix, 'share/elasticluster/providers/ansible-playbooks'),
        'ansible_module_dir': os.path.join(
            sys.prefix,
            'share/elasticluster/providers/ansible-playbooks/modules'),
    }

    def __init__(self):
        # will be initialized upon user input from outside
        self.file_path = None
        self.cluster_name = None
        self.storage_path = None

        self._config = QuotelessConfigParser(self.config_defaults)

    def _read_section(self, name):
        """
        Reads a section from the configuration file and returns a
        dictionary with its content
        """
        self._config.read(self.file_path)
        if self._config.has_section(name):
            return dict(self._config.items(name))
        else:
            raise ConfigParser.NoSectionError("section %s not found in "
                                              "configuration file" % name)

    def read_cluster_section(self, name):
        """
        Reads the cluster section for a given cluster name from the
        configuration file and returns its properties in a dictionary.
        """
        config = self._read_section("cluster/"+name)

        config_login = self.read_login_section(config["login"])

        return dict(config.items() + config_login.items())

    def read_node_section(self, cluster_name, node_type):
        """
        Reads the cluster configuration from the current config file
        with the given parameters for cluster and node type.  In this
        case, the sectoins of the cluster and the specific node types
        will be merged (node_type is more specific) in order to allow
        easier configuration options.
        """

        config_name_general = "cluster/" + cluster_name
        config_name_specific = "cluster/" + cluster_name + "/" + node_type

        # merge configuration parts from the cluster and
        # compute/frontend section
        if self._config.has_section(config_name_general):
            if self._config.has_section(config_name_specific):
                config = dict(self.read_cluster_section(cluster_name).items() +
                              self._read_section(config_name_specific).items())
            else:
                config = self.read_cluster_section(cluster_name)

            return config

        else:
            raise ConfigParser.NoSectionError(
                "no configuration secton for cluster `%s`"
                " found" % cluster_name)

    def read_cloud_section(self, name):
        """
        Reads the cloud section for a given cloud name from the
        configuraiton file and returns its properties in a dictionary.
        """
        config = self._read_section("cloud/"+name)

        return config

    def read_setup_section(self, name, cluster_name):
        """
        Reads the setup section for a given setup name from the
        configuration file and returns its properties in a dictionary
        """
        config = self._read_section("setup/"+name)

        config["playbook_path"] = os.path.expanduser(
            os.path.expanduser(config["playbook_path"]))

        login_name = self.read_cluster_section(cluster_name)["login"]

        config_login = self.read_login_section(login_name)

        return dict(config.items() + config_login.items())

    def read_login_section(self, name):
        """
        Reads the login section for the given name from the
        configuration file and returns its properties in a dictionary
        """
        config = self._read_section("login/" + name)
        config["user_key_private"] = os.path.expanduser(
            os.path.expandvars(config["user_key_private"]))
        config["user_key_public"] = os.path.expanduser(
            os.path.expandvars(config["user_key_public"]))

        if (not os.path.exists(config["user_key_private"]) or
                not os.path.exists(config["user_key_public"])):
            log.warning("The key files don't exist. Please check your "
                        "configuration file `user_key_public`, "
                        "`user_key_private`.")

        return config

    def list_cluster_templates(self):
        """
        Return the list of cluster templates that are defined in the
        configuration file.
        """
        self._config.read(self.file_path)
        templates = []
        for section in self._config.sections():
            if section.startswith('cluster/'):
                templates.append(section.split('/')[1])
        return templates
