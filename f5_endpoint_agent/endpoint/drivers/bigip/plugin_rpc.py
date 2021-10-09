"""RPC API for calls back to the plugin."""
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

from oslo_log import helpers as log_helpers
from oslo_log import log as logging
import oslo_messaging as messaging

from neutron.common import rpc

from f5_endpoint_agent.endpoint.drivers.bigip import constants_v2 as constants

LOG = logging.getLogger(__name__)


class Endpointv2PluginRPC(object):
    """Client interface for agent to plugin RPC."""

    RPC_API_NAMESPACE = None

    def __init__(self, topic, context, env, group, host):
        """Initialize Endpointv2PluginRPC."""
        super(Endpointv2PluginRPC, self).__init__()

        if topic:
            self.topic = topic
        else:
            self.topic = constants.TOPIC_PROCESS_ON_HOST_V2

        self.target = messaging.Target(topic=self.topic,
                                       version=constants.RPC_API_VERSION)
        self._client = rpc.get_client(self.target, version_cap=None)

        self.context = context
        self.env = env
        self.group = group
        self.host = host

    def _make_msg(self, method, **kwargs):
        return {'method': method,
                'namespace': self.RPC_API_NAMESPACE,
                'args': kwargs}

    def _call(self, context, msg, **kwargs):
        return self.__call_rpc_method(context,
                                      msg,
                                      rpc_method='call',
                                      **kwargs)

    def _cast(self, context, msg, **kwargs):
        self.__call_rpc_method(context,
                               msg,
                               rpc_method='cast',
                               **kwargs)

    def _fanout_cast(self, context, msg, **kwargs):
        kwargs['fanout'] = True
        self.__call_rpc_method(context, msg, rpc_method='cast', **kwargs)

    def __call_rpc_method(self, context, msg, **kwargs):
        options = dict(
            ((opt, kwargs[opt])
             for opt in ('fanout', 'timeout', 'topic', 'version')
             if kwargs.get(opt))
        )
        if msg['namespace']:
            options['namespace'] = msg['namespace']

        if options:
            callee = self._client.prepare(**options)
        else:
            callee = self._client

        func = getattr(callee, kwargs['rpc_method'])
        return func(context, msg['method'], **msg['args'])
