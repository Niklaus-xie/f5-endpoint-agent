"""F5 Endpoint constants for agent."""
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

# Service resync interval
RESYNC_INTERVAL = 300

AGENT_BINARY_NAME = 'f5-endpoint-agent'

# RPC channel names
# use the same
TOPIC_PROCESS_ON_HOST_V2 = 'f5-endpoint-process-on-controller'
TOPIC_ENDPOINT_AGENT_V2 = 'f5-endpoint-process-on-agent'

RPC_API_VERSION = '1.0'
# RPC_API_NAMESPACE = ""

FDB_POPULATE_STATIC_ARP = True
# for test only
MIN_EXTRA_MB = 0
# MIN_EXTRA_MB = 500

MIN_TMOS_MAJOR_VERSION = 11
MIN_TMOS_MINOR_VERSION = 0

DEVICE_CONNECTION_TIMEOUT = 5

# Neutron-endpoint and Neutron constants
F5_AGENT_TYPE_ENDPOINT = 'Endpoint agent'
F5_STATS_IN_BYTES = 'bytes_in'
F5_STATS_OUT_BYTES = 'bytes_out'
F5_STATS_ACTIVE_CONNECTIONS = 'active_connections'
F5_STATS_TOTAL_CONNECTIONS = 'total_connections'
F5_OFFLINE = 'OFFLINE'
F5_ONLINE = 'ONLINE'
F5_DISABLED = 'DISABLED'
F5_NO_MONITOR = 'NO_MONITOR'
F5_CHECKING = 'CHECKING'

F5_ACTIVE = 'ACTIVE'
F5_PENDING_CREATE = "PENDING_CREATE"
F5_PENDING_UPDATE = "PENDING_UPDATE"
F5_PENDING_DELETE = "PENDING_DELETE"
F5_ERROR = "ERROR"

ACTIVE = 'ACTIVE'
PENDING_CREATE = "PENDING_CREATE"
PENDING_UPDATE = "PENDING_UPDATE"
PENDING_DELETE = "PENDING_DELETE"
ERROR = "ERROR"

F5_FLOODING_ENTRY = ('00:00:00:00:00:00', '0.0.0.0')

PLUGIN = 'q-plugin'
UPDATE = 'update'
L2POPULATION = 'l2population'
AGENT = 'q-agent-notifier'
