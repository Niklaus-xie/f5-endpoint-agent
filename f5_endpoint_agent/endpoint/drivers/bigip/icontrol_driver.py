# coding=utf-8#
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

import base64
import datetime
# import hashlib
# import json
import logging as std_logging
import os
import signal
# import urllib

# from eventlet import greenthread
from time import strftime
# from time import time

# from requests import HTTPError

from oslo_config import cfg
# from oslo_log import helpers as log_helpers
from oslo_log import log as logging
from oslo_utils import importutils

from f5_endpoint_agent.endpoint.drivers.bigip import exceptions as f5ex

from f5.bigip import ManagementRoot
from f5_endpoint_agent.endpoint.drivers.bigip import constants_v2 as f5const
from f5_endpoint_agent.endpoint.drivers.bigip import stat_helper
from f5_endpoint_agent.endpoint.drivers.bigip.utils import serialized

from f5_endpoint_agent.endpoint.drivers.bigip.endpoint_driver import \
    EndpointBaseDriver

LOG = logging.getLogger(__name__)

__VERSION__ = '0.1.1'


OPTS = [
    cfg.StrOpt(
        'f5_ha_type', default='pair',
        help='Are we standalone, pair(active/standby), or scalen'
    ),
    cfg.StrOpt(
        'icontrol_hostname',
        default="10.190.5.7",
        help='The hostname (name or IP address) to use for iControl access'
    ),
    cfg.StrOpt(
        'icontrol_username', default='admin',
        help='The username to use for iControl access'
    ),
    cfg.StrOpt(
        'icontrol_password', default='admin', secret=True,
        help='The password to use for iControl access'
    ),
    cfg.IntOpt(
        'icontrol_connection_timeout', default=30,
        help='How many seconds to timeout a connection to BIG-IP'
    ),
    cfg.IntOpt(
        'icontrol_connection_retry_interval', default=10,
        help='How many seconds to wait between retry connection attempts'
    ),
    cfg.StrOpt(
        'icontrol_config_mode', default='objects',
        help='Whether to use iapp or objects for bigip configuration'
    ),
    cfg.StrOpt(
        'auth_version',
        default=None,
        help='Keystone authentication version (v2 or v3) for Barbican client.'
    ),
    cfg.StrOpt(
        'os_project_id',
        default='service',
        help='OpenStack project ID.'
    ),
    cfg.StrOpt(
        'os_auth_url',
        default=None,
        help='OpenStack authentication URL.'
    ),
    cfg.StrOpt(
        'os_username',
        default=None,
        help='OpenStack user name for Keystone authentication.'
    ),
    cfg.StrOpt(
        'os_user_domain_name',
        default=None,
        help='OpenStack user domain name for Keystone authentication.'
    ),
    cfg.StrOpt(
        'os_project_name',
        default=None,
        help='OpenStack project name for Keystone authentication.'
    ),
    cfg.StrOpt(
        'os_project_domain_name',
        default=None,
        help='OpenStack domain name for Keystone authentication.'
    ),
    cfg.StrOpt(
        'os_password',
        default=None,
        help='OpenStack user password for Keystone authentication.'
    ),
    cfg.StrOpt(
        'f5_parent_ssl_profile',
        default='clientssl',
        help='Parent profile used when creating client SSL profiles '
        'for listeners with TERMINATED_HTTPS protocols.'
    ),
    cfg.StrOpt(
        'os_tenant_name',
        default=None,
        help='OpenStack tenant name for Keystone authentication (v2 only).'
    )
]


def is_operational(method):
    # Decorator to check we are operational before provisioning.
    def wrapper(*args, **kwargs):
        instance = args[0]
        if instance.operational:
            try:
                return method(*args, **kwargs)
            except IOError as ioe:
                LOG.error('IO Error detected: %s' % method.__name__)
                LOG.error(str(ioe))
                raise ioe
        else:
            LOG.error('Cannot execute %s. Not operational. Re-initializing.'
                      % method.__name__)
            instance._init_bigips()
    return wrapper


class iControlDriver(EndpointBaseDriver):
    """Control service deployment."""

    positive_plugin_const_state = \
        tuple([f5const.F5_ACTIVE, f5const.F5_PENDING_CREATE,
               f5const.F5_PENDING_UPDATE])

    def __init__(self, conf, registerOpts=True):
        # The registerOpts parameter allows a test to
        # turn off config option handling so that it can
        # set the options manually instead.
        super(iControlDriver, self).__init__(conf)
        self.conf = conf
        if registerOpts:
            self.conf.register_opts(OPTS)
        self.initialized = False
        self.hostnames = None
        self.device_type = conf.f5_device_type
        self.plugin_rpc = None  # overrides base, same value
        self.agent_report_state = None  # overrides base, same value
        self.operational = False  # overrides base, same value
        self.driver_name = 'f5-endpoint-icontrol'

        #
        # BIG-IP containers
        #

        # BIG-IPs which currectly active
        self.__bigips = {}
        self.__last_connect_attempt = None

        # HA and traffic group validation
        self.ha_validated = False
        self.tg_initialized = False
        # traffic groups discovered from BIG-IPs for service placement
        self.__traffic_groups = []

        # base configurations to report to Neutron agent state reports
        self.agent_configurations = {}  # overrides base, same value
        self.agent_configurations['device_drivers'] = [self.driver_name]
        self.agent_configurations['icontrol_endpoints'] = {}

        # to store the verified esd names
        self.esd_names = []
        self.esd_processor = None

        # service component managers
        self.tenant_manager = None
        self.cluster_manager = None
        self.system_helper = None
        self.lbaas_builder = None
        self.service_adapter = None
        self.vlan_binding = None
        self.l3_binding = None
        self.cert_manager = None  # overrides register_OPTS

        # server helpers
        self.stat_helper = stat_helper.StatHelper()
        # self.network_helper = network_helper.NetworkHelper()

        if self.conf.password_cipher_mode:
            self.conf.icontrol_password = \
                base64.b64decode(self.conf.icontrol_password)
            if self.conf.os_password:
                self.conf.os_password = base64.b64decode(self.conf.os_password)

        try:

            # debug logging of service requests recieved by driver
            if self.conf.trace_service_requests:
                path = '/var/log/neutron/service/'
                if not os.path.exists(path):
                    os.makedirs(path)
                self.file_name = path + strftime("%H%M%S-%m%d%Y") + '.json'
                with open(self.file_name, 'w') as fp:
                    fp.write('[{}] ')

            # driver mode settings - GRM vs L2 adjacent
            if self.conf.f5_global_routed_mode:
                LOG.info('WARNING - f5_global_routed_mode enabled.'
                         ' There will be no L2 or L3 orchestration'
                         ' or tenant isolation provisioned. All vips'
                         ' and pool members must be routable through'
                         ' pre-provisioned SelfIPs.')
                self.conf.use_namespaces = False
                self.conf.f5_snat_mode = True
                self.conf.f5_snat_addresses_per_subnet = 0
                self.agent_configurations['tunnel_types'] = []
                self.agent_configurations['bridge_mappings'] = {}
            else:
                self.agent_configurations['tunnel_types'] = \
                    self.conf.advertised_tunnel_types
                for net_id in self.conf.common_network_ids:
                    LOG.debug('network %s will be mapped to /Common/%s'
                              % (net_id, self.conf.common_network_ids[net_id]))

                self.agent_configurations['common_networks'] = \
                    self.conf.common_network_ids
                LOG.debug('Setting static ARP population to %s'
                          % self.conf.f5_populate_static_arp)
                self.agent_configurations['f5_common_external_networks'] = \
                    self.conf.f5_common_external_networks
                f5const.FDB_POPULATE_STATIC_ARP = \
                    self.conf.f5_populate_static_arp

            # parse the icontrol_hostname setting
            self._init_bigip_hostnames()
            # instantiate the managers
            self._init_bigip_managers()

            self.initialized = True
            LOG.debug('iControlDriver loaded successfully')
        except Exception as exc:
            LOG.error("exception in intializing driver %s" % str(exc))
            self._set_agent_status(False)

    def connect(self):
        # initialize communications with BIG-IP via iControl
        try:
            self._init_bigips()
        except Exception as exc:
            LOG.error("exception in intializing communications to BIG-IPs %s"
                      % str(exc))
            self._set_agent_status(False)

    def _init_bigip_managers(self):

        if self.conf.vlan_binding_driver:
            try:
                self.vlan_binding = importutils.import_object(
                    self.conf.vlan_binding_driver, self.conf, self)
            except ImportError:
                LOG.error('Failed to import VLAN binding driver: %s'
                          % self.conf.vlan_binding_driver)

        if self.conf.l3_binding_driver:
            try:
                self.l3_binding = importutils.import_object(
                    self.conf.l3_binding_driver, self.conf, self)
            except ImportError:
                LOG.error('Failed to import L3 binding driver: %s'
                          % self.conf.l3_binding_driver)
        else:
            LOG.debug('No L3 binding driver configured.'
                      ' No L3 binding will be done.')

        if self.conf.cert_manager:
            try:
                self.cert_manager = importutils.import_object(
                    self.conf.cert_manager, self.conf)
            except ImportError as import_err:
                LOG.error('Failed to import CertManager: %s.' %
                          import_err.message)
                raise
            except Exception as err:
                LOG.error('Failed to initialize CertManager. %s' % err.message)
                # re-raise as ImportError to cause agent exit
                raise ImportError(err.message)

    def _init_bigip_hostnames(self):
        # Validate and parse bigip credentials
        if not self.conf.icontrol_hostname:
            raise f5ex.F5InvalidConfigurationOption(
                opt_name='icontrol_hostname',
                opt_value='valid hostname or IP address'
            )
        if not self.conf.icontrol_username:
            raise f5ex.F5InvalidConfigurationOption(
                opt_name='icontrol_username',
                opt_value='valid username'
            )
        if not self.conf.icontrol_password:
            raise f5ex.F5InvalidConfigurationOption(
                opt_name='icontrol_password',
                opt_value='valid password'
            )

        self.hostnames = self.conf.icontrol_hostname.split(',')
        self.hostnames = [item.strip() for item in self.hostnames]
        self.hostnames = sorted(self.hostnames)

        # initialize per host agent_configurations
        for hostname in self.hostnames:
            self.__bigips[hostname] = bigip = type('', (), {})()
            bigip.hostname = hostname
            bigip.status = 'creating'
            bigip.status_message = 'creating BIG-IP from iControl hostnames'
            bigip.device_interfaces = dict()
            self.agent_configurations[
                'icontrol_endpoints'][hostname] = {}
            self.agent_configurations[
                'icontrol_endpoints'][hostname]['failover_state'] = \
                'undiscovered'
            self.agent_configurations[
                'icontrol_endpoints'][hostname]['status'] = 'unknown'
            self.agent_configurations[
                'icontrol_endpoints'][hostname]['status_message'] = ''

    def _init_bigips(self):
        # Connect to all BIG-IPs
        LOG.debug('initializing communications to BIG-IPs')
        try:
            # setup logging options
            if not self.conf.debug:
                requests_log = std_logging.getLogger(
                    "requests.packages.urllib3")
                requests_log.setLevel(std_logging.ERROR)
                requests_log.propagate = False
            else:
                requests_log = std_logging.getLogger(
                    "requests.packages.urllib3")
                requests_log.setLevel(std_logging.DEBUG)
                requests_log.propagate = True

            self.__last_connect_attempt = datetime.datetime.now()

            for hostname in self.hostnames:
                # connect to each BIG-IP and set it status
                bigip = self._open_bigip(hostname)
                if bigip.status == 'active':
                    continue

                if bigip.status == 'connected':
                    # set the status down until we assure initialized
                    bigip.status = 'initializing'
                    bigip.status_message = 'initializing HA viability'
                    LOG.debug('initializing HA viability %s' % hostname)
                    device_group_name = None
                    if not self.ha_validated:
                        device_group_name = self._validate_ha(bigip)
                        LOG.debug('HA validated from %s with DSG %s' %
                                  (hostname, device_group_name))
                        self.ha_validated = True
                    if not self.tg_initialized:
                        self._init_traffic_groups(bigip)
                        LOG.debug('learned traffic groups from %s as %s' %
                                  (hostname, self.__traffic_groups))
                        self.tg_initialized = True
                    LOG.debug('initializing bigip %s' % hostname)
                    self._init_bigip(bigip, hostname, device_group_name)
                    LOG.debug('initializing agent configurations %s'
                              % hostname)
                    self._init_agent_config(bigip)
                    # Assure basic BIG-IP HA is operational
                    LOG.debug('validating HA state for %s' % hostname)
                    bigip.status = 'validating_HA'
                    bigip.status_message = 'validating the current HA state'
                    if self._validate_ha_operational(bigip):
                        LOG.debug('setting status to active for %s' % hostname)
                        bigip.status = 'active'
                        bigip.status_message = 'BIG-IP ready for provisioning'
                        self._post_init()
                    else:
                        LOG.debug('setting status to error for %s' % hostname)
                        bigip.status = 'error'
                        bigip.status_message = 'BIG-IP is not operational'
                        self._set_agent_status(False)
                else:
                    LOG.error('error opening BIG-IP %s - %s:%s'
                              % (hostname, bigip.status, bigip.status_message))
                    self._set_agent_status(False)
        except Exception as exc:
            LOG.error('Invalid agent configuration: %s' % exc.message)
            raise
        self._set_agent_status(force_resync=True)

    def _init_errored_bigips(self):
        try:
            errored_bigips = self.get_errored_bigips_hostnames()
            if errored_bigips:
                LOG.debug('attempting to recover %s BIG-IPs' %
                          len(errored_bigips))
                for hostname in errored_bigips:
                    # try to connect and set status
                    bigip = self._open_bigip(hostname)
                    if bigip.status == 'connected':
                        # set the status down until we assure initialized
                        bigip.status = 'initializing'
                        bigip.status_message = 'initializing HA viability'
                        LOG.debug('initializing HA viability %s' % hostname)
                        LOG.debug('proceeding to initialize %s' % hostname)
                        device_group_name = None
                        if not self.ha_validated:
                            device_group_name = self._validate_ha(bigip)
                            LOG.debug('HA validated from %s with DSG %s' %
                                      (hostname, device_group_name))
                            self.ha_validated = True
                        if not self.tg_initialized:
                            self._init_traffic_groups(bigip)
                            LOG.debug('known traffic groups initialized',
                                      ' from %s as %s' %
                                      (hostname, self.__traffic_groups))
                            self.tg_initialized = True
                        LOG.debug('initializing bigip %s' % hostname)
                        self._init_bigip(bigip, hostname, device_group_name)
                        LOG.debug('initializing agent configurations %s'
                                  % hostname)
                        self._init_agent_config(bigip)

                        # Assure basic BIG-IP HA is operational
                        LOG.debug('validating HA state for %s' % hostname)
                        bigip.status = 'validating_HA'
                        bigip.status_message = \
                            'validating the current HA state'
                        if self._validate_ha_operational(bigip):
                            LOG.debug('setting status to active for %s'
                                      % hostname)
                            bigip.status = 'active'
                            bigip.status_message = \
                                'BIG-IP ready for provisioning'
                            self._post_init()
                            self._set_agent_status(True)
                        else:
                            LOG.debug('setting status to error for %s'
                                      % hostname)
                            bigip.status = 'error'
                            bigip.status_message = 'BIG-IP is not operational'
                            self._set_agent_status(False)
            else:
                LOG.debug('there are no BIG-IPs with error status')
        except Exception as exc:
            LOG.error('Invalid agent configuration: %s' % exc.message)
            raise

    def _open_bigip(self, hostname):
        # Open bigip connection
        try:
            bigip = self.__bigips[hostname]
            # active creating connected initializing validating_HA
            # error connecting. If status is e.g. 'initializing' etc,
            # seems should try to connect again, otherwise status stucks
            # the same forever.
            if bigip.status in ['active']:
                LOG.debug('BIG-IP %s status invalid %s to open a connection'
                          % (hostname, bigip.status))
                return bigip
            bigip.status = 'connecting'
            bigip.status_message = 'requesting iControl endpoint'
            LOG.info('opening iControl connection to %s @ %s' %
                     (self.conf.icontrol_username, hostname))
            bigip = ManagementRoot(hostname,
                                   self.conf.icontrol_username,
                                   self.conf.icontrol_password,
                                   timeout=f5const.DEVICE_CONNECTION_TIMEOUT,
                                   debug=self.conf.debug)
            bigip.status = 'connected'
            bigip.status_message = 'connected to BIG-IP'
            self.__bigips[hostname] = bigip
            return bigip
        except Exception as exc:
            LOG.error('could not communicate with ' +
                      'iControl device: %s' % hostname)
            # pzhang: reset the signal from icontrol sdk
            signal.alarm(0)
            # since no bigip object was created, create a dummy object
            # so we can store the status and status_message attributes
            errbigip = type('', (), {})()
            errbigip.hostname = hostname
            errbigip.status = 'error'
            errbigip.status_message = str(exc)[:80]
            self.__bigips[hostname] = errbigip
            return errbigip

    def _init_bigip(self, bigip, hostname, check_group_name=None):
        # Prepare a bigip for usage
        try:
            major_version, minor_version = self._validate_bigip_version(
                bigip, hostname)

            device_group_name = None
            extramb = self.system_helper.get_provision_extramb(bigip)
            if int(extramb) < f5const.MIN_EXTRA_MB:
                raise f5ex.ProvisioningExtraMBValidateFailed(
                    'Device %s BIG-IP not provisioned for '
                    'management LARGE.' % hostname)

            if self.conf.f5_ha_type == 'pair' and \
                    self.cluster_manager.get_sync_status(bigip) == \
                    'Standalone':
                raise f5ex.BigIPClusterInvalidHA(
                    'HA mode is pair and bigip %s in standalone mode'
                    % hostname)

            if self.conf.f5_ha_type == 'scalen' and \
                    self.cluster_manager.get_sync_status(bigip) == \
                    'Standalone':
                raise f5ex.BigIPClusterInvalidHA(
                    'HA mode is scalen and bigip %s in standalone mode'
                    % hostname)

            if self.conf.f5_ha_type != 'standalone':
                device_group_name = \
                    self.cluster_manager.get_device_group(bigip)
                if not device_group_name:
                    raise f5ex.BigIPClusterInvalidHA(
                        'HA mode is %s and no sync failover '
                        'device group found for device %s.'
                        % (self.conf.f5_ha_type, hostname))
                if check_group_name and device_group_name != check_group_name:
                    raise f5ex.BigIPClusterInvalidHA(
                        'Invalid HA. Device %s is in device group'
                        ' %s but should be in %s.'
                        % (hostname, device_group_name, check_group_name))
                bigip.device_group_name = device_group_name

            if self.network_builder:
                for network in self.conf.common_network_ids.values():
                    if not self.network_builder.vlan_exists(bigip,
                                                            network,
                                                            folder='Common'):
                        raise f5ex.MissingNetwork(
                            'Common network %s on %s does not exist'
                            % (network, bigip.hostname))
            bigip.device_name = self.cluster_manager.get_device_name(bigip)
            bigip.mac_addresses = self.system_helper.get_mac_addresses(bigip)
            LOG.debug("Initialized BIG-IP %s with MAC addresses %s" %
                      (bigip.device_name, ', '.join(bigip.mac_addresses)))
            bigip.device_interfaces = \
                self.system_helper.get_interface_macaddresses_dict(bigip)
            bigip.assured_networks = {}
            bigip.assured_tenant_snat_subnets = {}
            bigip.assured_gateway_subnets = []

            if self.conf.f5_ha_type != 'standalone':
                self.cluster_manager.disable_auto_sync(
                    device_group_name, bigip)

            # validate VTEP SelfIPs
            if not self.conf.f5_global_routed_mode:
                self.network_builder.initialize_tunneling(bigip)

            # Turn off tunnel syncing between BIG-IP
            # as our VTEPs properly use only local SelfIPs
            if self.system_helper.get_tunnel_sync(bigip) == 'enable':
                self.system_helper.set_tunnel_sync(bigip, enabled=False)

            LOG.debug('connected to iControl %s @ %s ver %s.%s'
                      % (self.conf.icontrol_username, hostname,
                         major_version, minor_version))
        except Exception as exc:
            bigip.status = 'error'
            bigip.status_message = str(exc)[:80]
            raise
        return bigip

    def _post_init(self):
        # After we have a connection to the BIG-IPs, initialize vCMP
        # on all connected BIG-IPs
        if self.network_builder:
            self.network_builder.initialize_vcmp()

        self.agent_configurations['network_segment_physical_network'] = \
            self.conf.f5_network_segment_physical_network

        LOG.info('iControlDriver initialized to %d bigips with username:%s'
                 % (len(self.get_active_bigips()),
                    self.conf.icontrol_username))
        LOG.info('iControlDriver dynamic agent configurations:%s'
                 % self.agent_configurations)

        if self.vlan_binding:
            LOG.debug(
                'getting BIG-IP device interface for VLAN Binding')
            self.vlan_binding.register_bigip_interfaces()

        if self.l3_binding:
            LOG.debug('getting BIG-IP MAC Address for L3 Binding')
            self.l3_binding.register_bigip_mac_addresses()

        # endpoints = self.agent_configurations['icontrol_endpoints']
        # for ic_host in endpoints.keys():
        for hostbigip in self.get_all_bigips():

            # hostbigip = self.__bigips[ic_host]
            mac_addrs = [mac_addr for interface, mac_addr in
                         hostbigip.device_interfaces.items()
                         if interface != "mgmt"]
            ports = self.plugin_rpc.get_ports_for_mac_addresses(
                mac_addresses=mac_addrs)
            if ports:
                self.agent_configurations['nova_managed'] = True
            else:
                self.agent_configurations['nova_managed'] = False

        if self.network_builder:
            self.network_builder.post_init()

        self._set_agent_status(False)

    def _validate_ha(self, bigip):
        # if there was only one address supplied and
        # this is not a standalone device, get the
        # devices trusted by this device.
        device_group_name = None
        if self.conf.f5_ha_type == 'standalone':
            if len(self.hostnames) != 1:
                bigip.status = 'error'
                bigip.status_message = \
                    'HA mode is standalone and %d hosts found.'\
                    % len(self.hostnames)
                raise f5ex.BigIPClusterInvalidHA(
                    'HA mode is standalone and %d hosts found.'
                    % len(self.hostnames))
            device_group_name = 'standalone'
        elif self.conf.f5_ha_type == 'pair':
            device_group_name = self.cluster_manager.\
                get_device_group(bigip)
            if len(self.hostnames) != 2:
                mgmt_addrs = []
                devices = self.cluster_manager.devices(bigip)
                for device in devices:
                    mgmt_addrs.append(
                        self.cluster_manager.get_mgmt_addr_by_device(
                            bigip, device))
                self.hostnames = mgmt_addrs
            if len(self.hostnames) != 2:
                bigip.status = 'error'
                bigip.status_message = 'HA mode is pair and %d hosts found.' \
                    % len(self.hostnames)
                raise f5ex.BigIPClusterInvalidHA(
                    'HA mode is pair and %d hosts found.'
                    % len(self.hostnames))
        elif self.conf.f5_ha_type == 'scalen':
            device_group_name = self.cluster_manager.\
                get_device_group(bigip)
            if len(self.hostnames) < 2:
                mgmt_addrs = []
                devices = self.cluster_manager.devices(bigip)
                for device in devices:
                    mgmt_addrs.append(
                        self.cluster_manager.get_mgmt_addr_by_device(
                            bigip, device)
                    )
                self.hostnames = mgmt_addrs
            if len(self.hostnames) < 2:
                bigip.status = 'error'
                bigip.status_message = 'HA mode is scale and 1 hosts found.'
                raise f5ex.BigIPClusterInvalidHA(
                    'HA mode is pair and 1 hosts found.')
        return device_group_name

    def _validate_ha_operational(self, bigip):
        if self.conf.f5_ha_type == 'standalone':
            return True
        else:
            # how many active BIG-IPs are there?
            active_bigips = self.get_active_bigips()
            if active_bigips:
                sync_status = self.cluster_manager.get_sync_status(bigip)
                if sync_status in ['Disconnected', 'Sync Failure']:
                    if len(active_bigips) > 1:
                        # the device should not be in the disconnected state
                        return False
                if len(active_bigips) > 1:
                    # it should be in the same sync-failover group
                    # as the rest of the active bigips
                    device_group_name = \
                        self.cluster_manager.get_device_group(bigip)
                    for active_bigip in active_bigips:
                        adgn = self.cluster_manager.get_device_group(
                            active_bigip)
                        if not adgn == device_group_name:
                            return False
                return True
            else:
                return True

    def _init_agent_config(self, bigip):
        # Init agent config
        ic_host = {}
        ic_host['version'] = self.system_helper.get_version(bigip)
        ic_host['device_name'] = bigip.device_name
        ic_host['platform'] = self.system_helper.get_platform(bigip)
        ic_host['serial_number'] = self.system_helper.get_serial_number(bigip)
        ic_host['status'] = bigip.status
        ic_host['status_message'] = bigip.status_message
        ic_host['failover_state'] = self.get_failover_state(bigip)
        if hasattr(bigip, 'local_ip') and bigip.local_ip:
            ic_host['local_ip'] = bigip.local_ip
        else:
            ic_host['local_ip'] = 'VTEP disabled'
            self.agent_configurations['tunnel_types'] = list()
        self.agent_configurations['icontrol_endpoints'][bigip.hostname] = \
            ic_host
        if self.network_builder:
            self.agent_configurations['bridge_mappings'] = \
                self.network_builder.interface_mapping

    def _set_agent_status(self, force_resync=False):
        for hostname in self.__bigips:
            bigip = self.__bigips[hostname]
            self.agent_configurations[
                'icontrol_endpoints'][bigip.hostname][
                'status'] = bigip.status
            self.agent_configurations[
                'icontrol_endpoints'][bigip.hostname][
                'status_message'] = bigip.status_message

            if self.conf.report_esd_names_in_agent:
                LOG.debug('adding names to report:')
                self.agent_configurations['esd_name'] = \
                    self.get_valid_esd_names()
        # Policy - if any BIG-IP are active we're operational
        if self.get_active_bigips():
            self.operational = True
        else:
            self.operational = False
        if self.agent_report_state:
            self.agent_report_state(force_resync=force_resync)

    def get_failover_state(self, bigip):
        try:
            if hasattr(bigip, 'tm'):
                fs = bigip.tm.sys.dbs.db.load(name='failover.state')
                bigip.failover_state = fs.value
                return bigip.failover_state
            else:
                return 'error'
        except Exception as exc:
            LOG.exception('Error getting %s failover state' % bigip.hostname)
            bigip.status = 'error'
            bigip.status_message = str(exc)[:80]
            self._set_agent_status(False)
            return 'error'

    def get_agent_configurations(self):
        for hostname in self.__bigips:
            bigip = self.__bigips[hostname]
            if bigip.status == 'active':
                failover_state = self.get_failover_state(bigip)
                self.agent_configurations[
                    'icontrol_endpoints'][bigip.hostname][
                    'failover_state'] = failover_state
            else:
                self.agent_configurations[
                    'icontrol_endpoints'][bigip.hostname][
                    'failover_state'] = 'unknown'
            self.agent_configurations['icontrol_endpoints'][
                bigip.hostname]['status'] = bigip.status
            self.agent_configurations['icontrol_endpoints'][
                bigip.hostname]['status_message'] = bigip.status_message
            self.agent_configurations['operational'] = \
                self.operational
        LOG.debug('agent configurations are: %s' % self.agent_configurations)
        return dict(self.agent_configurations)

    def recover_errored_devices(self):
        # trigger a retry on errored BIG-IPs
        try:
            self._init_errored_bigips()
        except Exception as exc:
            LOG.error('Could not recover devices: %s' % exc.message)

    def backend_integrity(self):
        if self.operational:
            return True
        return False

    def generate_capacity_score(self, capacity_policy=None):
        """Generate the capacity score of connected devices."""
        if capacity_policy:
            highest_metric = 0.0
            highest_metric_name = None
            my_methods = dir(self)
            bigips = self.get_all_bigips()
            for metric in capacity_policy:
                func_name = 'get_' + metric
                if func_name in my_methods:
                    max_capacity = int(capacity_policy[metric])
                    metric_func = getattr(self, func_name)
                    metric_value = 0
                    for bigip in bigips:
                        if bigip.status == 'active':
                            global_stats = \
                                self.stat_helper.get_global_statistics(bigip)
                            value = int(
                                metric_func(bigip=bigip,
                                            global_statistics=global_stats)
                            )
                            LOG.debug('calling capacity %s on %s returned: %s'
                                      % (func_name, bigip.hostname, value))
                        else:
                            value = 0
                        if value > metric_value:
                            metric_value = value
                    metric_capacity = float(metric_value) / float(max_capacity)
                    if metric_capacity > highest_metric:
                        highest_metric = metric_capacity
                        highest_metric_name = metric
                else:
                    LOG.warn('capacity policy has method '
                             '%s which is not implemented in this driver'
                             % metric)
            LOG.debug('capacity score: %s based on %s'
                      % (highest_metric, highest_metric_name))
            return highest_metric
        return 0

    def set_context(self, context):
        # Context to keep for database access
        if self.network_builder:
            self.network_builder.set_context(context)

    def set_plugin_rpc(self, plugin_rpc):
        # Provide Plugin RPC access
        self.plugin_rpc = plugin_rpc

    @serialized('create_endpoint')
    @is_operational
    def create_endpoint(self, endpoint, service):
        """Create endpoint."""
        return self._common_service_handler(service)

    @serialized('update_endpoint')
    @is_operational
    def update_endpoint(self, old_endpoint, endpoint, service):
        """Update endpoint."""
        return self._common_service_handler(service)

    @serialized('delete_endpoint')
    @is_operational
    def delete_endpoint(self, endpoint, service):
        """Delete endpoint."""
        LOG.debug("Deleting endpoint")
        return self._common_service_handler(
            service,
            delete_partition=True,
            delete_event=True)
