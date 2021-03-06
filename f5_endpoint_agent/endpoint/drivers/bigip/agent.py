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

import errno
import inspect
import service_launcher
import sys

import f5_endpoint_agent.lbaasv2.drivers.bigip.exceptions as exceptions

try:
    from oslo_config import cfg
    from oslo_log import log as oslo_logging
except ImportError as CriticalError:
    frame = inspect.getframeinfo(inspect.currentframe())
    CriticalError = \
        exceptions.F5MissingDependencies(message=str(CriticalError),
                                         frame=frame)
    sys.exit(CriticalError.errno)


try:
    from neutron.common import config as common_config
    from neutron.common import rpc as n_rpc
    try:
        from neutron.conf.agent import common as config
    except Exception:
        from neutron.agent.common import config

except ImportError as Error:
    pass

import f5_endpoint_agent.endpoint.drivers.bigip.agent_manager as manager
import f5_endpoint_agent.endpoint.drivers.bigip.constants_v2 as f5constants

LOG = oslo_logging.getLogger(__name__)

# add possible options here
OPTS = [
]


class F5AgentService(n_rpc.Service):
    """F5 Agent service class."""

    def start(self):
        """Start the F5 agent service."""
        self.tg.add_timer(
            cfg.CONF.periodic_interval,
            self.manager.run_periodic_tasks,
            None,
            None
        )
        super(F5AgentService, self).start()


def main():
    """F5 Endpoint agent for OpenStack."""
    cfg.CONF.register_opts(OPTS)
    cfg.CONF.register_opts(manager.OPTS)

    config.register_agent_state_opts_helper(cfg.CONF)
    config.register_root_helper(cfg.CONF)

    common_config.init(sys.argv[1:])
    config.setup_logging()

    mgr = manager.EndpointAgentManager(cfg.CONF)

    svc = F5AgentService(
        host=mgr.agent_host,
        topic=f5constants.TOPIC_ENDPOINT_AGENT_V2,
        manager=mgr
    )

    service_launch = service_launcher.F5ServiceLauncher(cfg.CONF)

    service_launch.launch_service(svc)
    service_launch.wait()


if __name__ == '__main__':
    try:
        Error
    except NameError:
        sys.exc_clear()
    else:
        # We already had an exception, ABORT!
        LOG.exception(str(Error))
        sys.exit(errno.ENOSYS)
    main()
