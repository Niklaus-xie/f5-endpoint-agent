"""Endpoint Agent manager to handle plugin to agent RPC and periodic tasks."""
# coding=utf-8
# Copyright (c) 2021, F5 Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
import sys
import uuid

from oslo_config import cfg
from oslo_log import helpers as log_helpers
from oslo_log import log as logging
import oslo_messaging
from oslo_service import loopingcall
from oslo_service import periodic_task
from oslo_utils import importutils

from neutron.agent import rpc as agent_rpc
from neutron.plugins.ml2.drivers.l2pop import rpc as l2pop_rpc
try:
    from neutron_lib import context as ncontext
except ImportError:
    from neutron import context as ncontext

from f5_endpoint_agent.endpoint.drivers.bigip import constants_v2
from f5_endpoint_agent.endpoint.drivers.bigip import plugin_rpc

LOG = logging.getLogger(__name__)

OPTS = [
    cfg.IntOpt(
        'periodic_interval',
        default=600,
        help='Seconds between periodic task runs'
    ),
    cfg.IntOpt(
        'resync_interval',
        default=-1,
        help='Seconds interval for resync task'
    ),
    cfg.IntOpt(
        'config_save_interval',
        default=60,
        help='Seconds interval for config save'
    ),
    cfg.IntOpt(
        'scrub_interval',
        default=-1,
        help='Seconds interval for resync task'
    ),
    cfg.BoolOpt(
        'start_agent_admin_state_up',
        default=True,
        help='Should the agent force its admin_state_up to True on boot'
    ),
    cfg.BoolOpt(
        'f5_global_routed_mode',
        default=True,
        help=('Disable all L2 and L3 integration in favor of global routing')
    ),
    cfg.StrOpt(
        'f5_bigip_endpoint_device_driver',
        default=('f5_endpoint_agent.endpoint.drivers.bigip.icontrol_driver.'
                 'iControlDriver'),
        help=('The driver used to provision endpoint BigIPs')
    ),
    cfg.StrOpt(
        'provider_name',
        default=None,
        help=('provider_name for snat pool addresses')
    ),
    cfg.StrOpt(
        'availability_zone',
        default=None,
        help=('availability_zone for agent reporting')
    ),
    cfg.StrOpt(
        'agent_id',
        default=None,
        help=('static agent ID to use with Neutron')
    ),
    cfg.StrOpt(
        'static_agent_configuration_data',
        default=None,
        help=('static name:value entries to add to the agent configurations')
    ),
    cfg.StrOpt(
        'environment_prefix',
        default='Project',
        help=('The object name prefix for this environment')
    ),
    cfg.BoolOpt(
        'environment_specific_plugin',
        default=True,
        help=('Use environment specific plugin topic')
    ),
    cfg.IntOpt(
        'environment_group_number',
        default=1,
        help=('Agent group number for the environment')
    ),
    cfg.BoolOpt(
        'password_cipher_mode',
        default=False,
        help='The flag indicating the password is plain text or not.'
    ),
    cfg.IntOpt(
        'f5_errored_services_timeout',
        default=60,
        help=(
            'Amount of time to wait for a errored service to become active')
    ),
]

PERIODIC_TASK_INTERVAL = 600


# TODO: to modify this
class LogicalServiceCache(object):
    """Manage a cache of known services."""

    class Service(object):
        """Inner classes used to hold values for weakref lookups."""

        def __init__(self, port_id, loadbalancer_id, tenant_id, agent_host):
            self.port_id = port_id
            self.loadbalancer_id = loadbalancer_id
            self.tenant_id = tenant_id
            self.agent_host = agent_host

        def __eq__(self, other):
            return self.__dict__ == other.__dict__

        def __hash__(self):
            return hash(
                (self.port_id,
                 self.loadbalancer_id,
                 self.tenant_id,
                 self.agent_host)
            )

    def __init__(self):
        """Initialize Service cache object."""
        LOG.debug("Initializing LogicalServiceCache")
        self.services = {}

    @property
    def size(self):
        """Return the number of services cached."""
        return len(self.services)

    def put(self, service, agent_host):
        """Add a service to the cache."""
        port_id = service['loadbalancer'].get('vip_port_id', None)
        loadbalancer_id = service['loadbalancer']['id']
        tenant_id = service['loadbalancer']['tenant_id']
        if loadbalancer_id not in self.services:
            s = self.Service(port_id, loadbalancer_id, tenant_id, agent_host)
            self.services[loadbalancer_id] = s
        else:
            s = self.services[loadbalancer_id]
            s.tenant_id = tenant_id
            s.port_id = port_id
            s.agent_host = agent_host

    def remove(self, service):
        """Remove a service from the cache."""
        if not isinstance(service, self.Service):
            loadbalancer_id = service['loadbalancer']['id']
        else:
            loadbalancer_id = service.loadbalancer_id
        if loadbalancer_id in self.services:
            del(self.services[loadbalancer_id])

    def remove_by_loadbalancer_id(self, loadbalancer_id):
        """Remove service by providing the loadbalancer id."""
        if loadbalancer_id in self.services:
            del(self.services[loadbalancer_id])

    def get_by_loadbalancer_id(self, loadbalancer_id):
        """Retreive service by providing the loadbalancer id."""
        return self.services.get(loadbalancer_id, None)

    def get_loadbalancer_ids(self):
        """Return a list of cached loadbalancer ids."""
        return self.services.keys()

    def get_tenant_ids(self):
        """Return a list of tenant ids in the service cache."""
        tenant_ids = {}
        for service in self.services:
            tenant_ids[service.tenant_id] = 1
        return tenant_ids.keys()

    def get_agent_hosts(self):
        """Return a list of agent ids stored in the service cache."""
        agent_hosts = {}
        for service in self.services:
            agent_hosts[service.agent_host] = 1
        return agent_hosts.keys()


class EndpointAgentManager(periodic_task.PeriodicTasks):
    """Periodic task that is an endpoint for plugin to agent RPC."""

    RPC_API_VERSION = '1.0'

    target = oslo_messaging.Target(version='1.0')

    def __init__(self, conf):
        """Initialize EndpointAgentManager."""
        super(EndpointAgentManager, self).__init__(conf)
        LOG.debug("Initializing EndpointAgentManager")
        LOG.debug("runtime environment: %s" % sys.version)

        self.conf = conf
        self.context = ncontext.get_admin_context_without_session()
        self.serializer = None

        global PERIODIC_TASK_INTERVAL
        PERIODIC_TASK_INTERVAL = self.conf.periodic_interval

        # Create the cache of provisioned services
        self.cache = LogicalServiceCache()
        self.last_resync = datetime.datetime.now()
        self.last_member_update = datetime.datetime.now()
        self.needs_resync = False
        self.plugin_rpc = None
        self.tunnel_rpc = None
        self.l2_pop_rpc = None
        self.state_rpc = None
        self.pending_services = {}

        # Load the driver.
        self._load_driver(conf)

        # Set the agent ID
        if self.conf.agent_id:
            self.agent_host = self.conf.agent_id
            LOG.debug('setting agent host to %s' % self.agent_host)
        else:
            # If not set statically, add the driver agent env hash
            agent_hash = str(
                uuid.uuid5(uuid.NAMESPACE_DNS,
                           self.conf.environment_prefix +
                           '.' + self.lbdriver.hostnames[0])
                )
            self.agent_host = conf.host + ":" + agent_hash
            LOG.debug('setting agent host to %s' % self.agent_host)

        # Initialize agent configurations
        agent_configurations = (
            {'environment_prefix': self.conf.environment_prefix,
             'environment_group_number': self.conf.environment_group_number,
             'global_routed_mode': self.conf.f5_global_routed_mode}
        )
        if self.conf.static_agent_configuration_data:
            entries = str(self.conf.static_agent_configuration_data).split(',')
            for entry in entries:
                nv = entry.strip().split(':')
                if len(nv) > 1:
                    agent_configurations[nv[0]] = nv[1]

        # Initialize agent-state to a default values
        self.admin_state_up = self.conf.start_agent_admin_state_up

        self.agent_state = {
            'binary': constants_v2.AGENT_BINARY_NAME,
            'host': self.agent_host,
            'topic': constants_v2.TOPIC_ENDPOINT_AGENT_V2,
            'agent_type': constants_v2.F5_AGENT_TYPE_ENDPOINT,
            'start_flag': True,
            'configurations': agent_configurations,
            'availability_zone': self.conf.availability_zone
        }

        # Setup RPC for communications to and from controller
        self._setup_rpc()

        # Set driver context for RPC.
        self.lbdriver.set_context(self.context)
        # Allow the driver to make callbacks to the LBaaS driver plugin
        self.lbdriver.set_plugin_rpc(self.plugin_rpc)
        # Allow the driver to update tunnel endpoints
        self.lbdriver.set_tunnel_rpc(self.tunnel_rpc)
        # Allow the driver to update forwarding records in the SDN
        self.lbdriver.set_l2pop_rpc(self.l2_pop_rpc)
        # Allow the driver to force and agent state report to the controller
        self.lbdriver.set_agent_report_state(self._report_state)

        # Set the flag to resync tunnels/services
        self.needs_resync = True

        # Mark this agent admin_state_up per startup policy
        if(self.admin_state_up):
            self.plugin_rpc.set_agent_admin_state(self.admin_state_up)

        # Start state reporting of agent to Neutron
        report_interval = self.conf.AGENT.report_interval
        if report_interval:
            heartbeat = loopingcall.FixedIntervalLoopingCall(
                self._report_state)
            heartbeat.start(interval=report_interval)

        if self.lbdriver:
            self.lbdriver.connect()

    def _load_driver(self, conf):
        self.lbdriver = None

        LOG.debug('loading LBaaS driver %s' %
                  conf.f5_bigip_endpoint_device_driver)
        try:
            self.lbdriver = importutils.import_object(
                conf.f5_bigip_endpoint_device_driver,
                self.conf)
            return
        except ImportError as ie:
            msg = ('Error importing endpoint device driver: %s error %s'
                   % (conf.f5_bigip_endpoint_device_driver, repr(ie)))
            LOG.error(msg)
            raise SystemExit(msg)

    def _setup_rpc(self):

        #
        # Setting up outbound (callbacks) communications from agent
        #

        # setup the topic to send oslo messages RPC calls
        # from this agent to the controller
        topic = constants_v2.TOPIC_PROCESS_ON_HOST_V2
        if self.conf.environment_specific_plugin:
            topic = topic + '_' + self.conf.environment_prefix
            LOG.debug('agent in %s environment will send callbacks to %s'
                      % (self.conf.environment_prefix, topic))

        # create our class we will use to send callbacks to the controller
        # for processing by the driver plugin
        self.plugin_rpc = plugin_rpc.Endpointv2PluginRPC(
            topic,
            self.context,
            self.conf.environment_prefix,
            self.conf.environment_group_number,
            self.agent_host
        )

        #
        # Setting up outbound communcations with the neutron agent extension
        #
        self.state_rpc = agent_rpc.PluginReportStateAPI(topic)

        #
        # Setting up all inbound notifications and outbound callbacks
        # for standard neutron agent services:
        #
        #     tunnel_sync - used to advertise the driver VTEP endpoints
        #                   and optionally learn about other VTEP endpoints
        #
        #     update - used to get updates to agent state triggered by
        #              the controller, like setting admin_state_up
        #              the agent
        #
        #     l2_populateion - used to get updates on neturon SDN topology
        #                      changes
        #
        #  We only establish notification if we care about L2/L3 updates
        #

        if not self.conf.f5_global_routed_mode:

            # notifications when tunnel endpoints get added
            self.tunnel_rpc = agent_rpc.PluginApi(constants_v2.PLUGIN)

            # define which controler notifications the agent comsumes
            consumers = [[constants_v2.TUNNEL, constants_v2.UPDATE]]

            # if we are dynamically changing tunnel peers,
            # register to recieve and send notificatoins via RPC
            if self.conf.l2_population:
                # communications of notifications from the
                # driver to neutron for SDN topology changes
                self.l2_pop_rpc = l2pop_rpc.L2populationAgentNotifyAPI()

                # notification of SDN topology updates from the
                # controller by adding to the general consumer list
                consumers.append(
                    [constants_v2.L2POPULATION,
                     constants_v2.UPDATE,
                     self.agent_host]
                )

            # kick off the whole RPC process by creating
            # a connection to the message bus
            self.endpoints = [self]
            self.connection = agent_rpc.create_consumers(
                self.endpoints,
                constants_v2.AGENT,
                consumers
            )

    def _report_state(self, force_resync=False):
        try:
            if force_resync:
                self.needs_resync = True
                self.cache.services = {}
                self.lbdriver.flush_cache()
            # use the admin_state_up to notify the
            # controller if all backend devices
            # are functioning properly. If not
            # automatically set the admin_state_up
            # for this agent to False
            if self.lbdriver:
                if not self.lbdriver.backend_integrity():
                    self.needs_resync = True
                    self.cache.services = {}
                    self.lbdriver.flush_cache()
                    self.plugin_rpc.set_agent_admin_state(False)
                    self.admin_state_up = False
                else:
                    # if we are transitioning from down to up,
                    # change the controller state for this agent
                    if not self.admin_state_up:
                        self.plugin_rpc.set_agent_admin_state(True)
                        self.admin_state_up = True

            if self.lbdriver:
                self.agent_state['configurations'].update(
                    self.lbdriver.get_agent_configurations()
                )

            # add the capacity score, used by the scheduler
            # for horizontal scaling of an environment, from
            # the driver
            if self.conf.capacity_policy:
                env_score = (
                    self.lbdriver.generate_capacity_score(
                        self.conf.capacity_policy
                    )
                )
                self.agent_state['configurations'][
                    'environment_capaciy_score'] = env_score
            else:
                self.agent_state['configurations'][
                    'environment_capacity_score'] = 0

            LOG.debug("reporting state of agent as: %s" % self.agent_state)
            self.state_rpc.report_state(self.context, self.agent_state)
            self.agent_state.pop('start_flag', None)

        except Exception as e:
            LOG.exception(("Failed to report state: " + str(e.message)))

    # callback from oslo messaging letting us know we are properly
    # connected to the message bus so we can register for inbound
    # messages to this agent
    def initialize_service_hook(self, started_by):
        """Create service hook to listen for messanges on agent topic."""
        node_topic = "%s_%s.%s" % (constants_v2.TOPIC_ENDPOINT_AGENT_V2,
                                   self.conf.environment_prefix,
                                   self.agent_host)
        LOG.debug("Creating topic for consuming messages: %s" % node_topic)
        endpoints = [started_by.manager]
        started_by.conn.create_consumer(
            node_topic, endpoints, fanout=False)

    @periodic_task.periodic_task(spacing=PERIODIC_TASK_INTERVAL)
    def connect_driver(self, context):
        """Trigger driver connect attempts to all devices."""
        if self.lbdriver:
            self.lbdriver.connect()

    @periodic_task.periodic_task(spacing=PERIODIC_TASK_INTERVAL)
    def recover_errored_devices(self, context):
        """Try to reconnect to errored devices."""
        if self.lbdriver:
            LOG.debug("running periodic task to retry errored devices")
            self.lbdriver.recover_errored_devices()

    ######################################################################
    #
    # handlers for all in bound requests and notifications from controller
    #
    ######################################################################
    @log_helpers.log_method_call
    def create_endpoint(self, context, endpoint, service):
        """Handle RPC cast from plugin to create endpoint."""
        pass

    @log_helpers.log_method_call
    def update_endpoint(self, context, old_endpoint, endpoint, service):
        """Handle RPC cast from plugin to update_endpoint."""
        pass

    @log_helpers.log_method_call
    def delete_endpoint(self, context, endpoint, service):
        """Handle RPC cast from plugin to delete_endpoint."""
        pass

    @log_helpers.log_method_call
    def create_ep_service(self, context, ep_service, service):
        """Handle RPC cast from plugin to create endpoint service."""
        pass

    @log_helpers.log_method_call
    def update_ep_service(self, context, old_ep_service, ep_service, service):
        """Handle RPC cast from plugin to update endpoint service."""
        pass

    @log_helpers.log_method_call
    def delete_ep_service(self, context, ep_service, service):
        """Handle RPC cast from plugin to delete endpoint service."""
        pass

    @log_helpers.log_method_call
    def agent_updated(self, context, payload):
        """Handle the agent_updated notification event."""
        if payload['admin_state_up'] != self.admin_state_up:
            LOG.info("agent administration status updated %s!", payload)
            self.admin_state_up = payload['admin_state_up']
            # the agent transitioned to down to up and the
            # driver reports healthy, trash the cache
            # and force an update to update agent scheduler
            if self.lbdriver.backend_integrity() and self.admin_state_up:
                self._report_state(True)
            else:
                self._report_state(False)
