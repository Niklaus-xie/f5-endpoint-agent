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


class EndpointBaseDriver(object):
    """Abstract base Endpoint Driver class to interface with Agent Manager."""

    def __init__(self, conf):
        self.agent_id = None
        self.plugin_rpc = None  # XXX overridden in the only known subclass
        self.connected = False  # XXX overridden in the only known subclass
        self.service_queue = []
        self.agent_configurations = {}  # XXX overridden in subclass

    def set_context(self, context):
        """Set the global context object for the lbaas driver."""
        raise NotImplementedError()

    def set_plugin_rpc(self, plugin_rpc):
        """Provide LBaaS Plugin RPC access."""

    def set_agent_report_state(self, report_state_callback):
        """Set Agent Report State."""
        raise NotImplementedError()
