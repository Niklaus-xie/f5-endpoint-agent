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
# import json
# import os
# import re
# import urllib

# from f5_endpoint_agent.endpoint.drivers.bigip import exceptions as f5_ex
# from f5_endpoint_agent.endpoint.drivers.bigip import resource_helper
# from f5_endpoint_agent.endpoint.drivers.bigip import tenants
# from f5_endpoint_agent.endpoint.drivers.bigip.utils import serialized
# from f5_endpoint_agent.endpoint.drivers.bigip import virtual_address
# from icontrol.exceptions import iControlUnexpectedHTTPError

from oslo_log import helpers as log_helpers
from oslo_log import log as logging

# from time import time

# from requests import HTTPError

LOG = logging.getLogger(__name__)


class ResourceManager(object):

    _collection_key = 'baseResources'
    _key = 'baseResource'

    def __init__(self, driver):
        self.driver = driver
        self.service_queue = driver.service_queue
        self.mutable_props = {}
        self.resource_helper = None

    def _shrink_payload(self, payload, **kwargs):
        keys_to_keep = kwargs.get('keys_to_keep', [])
        for key in payload.keys():
            if key not in keys_to_keep:
                del payload[key]

    def _search_element(self, resource, service):
        for element in service[self._collection_key]:
            if element['id'] == resource['id']:
                service[self._key] = element
                break

        if not service.get(self._key):
            raise Exception("Invalid input: %s %s "
                            "is not in service payload %s",
                            self._key, resource['id'], service)

    def _create_payload(self, resource, service):
        return {
            "name": "faked",
            "partition": "faked"
        }

    def _update_payload(self, old_resource, resource, service, **kwargs):
        payload = kwargs.get('payload', {})
        create_payload = kwargs.get('create_payload',
                                    self._create_payload(resource, service))

        for key in self.mutable_props.keys():
            old = old_resource.get(key)
            new = resource.get(key)
            if old != new:
                prop = self.mutable_props[key]
                payload[prop] = create_payload[prop]

        if len(payload.keys()) > 0:
            payload['name'] = create_payload['name']
            payload['partition'] = create_payload['partition']

        return payload

    def _create(self, bigip, payload, resource, service, **kwargs):
        resource_helper = kwargs.get("helper", self.resource_helper)
        resource_type = kwargs.get("type", self._resource)
        overwrite = kwargs.get("overwrite", True)
        if resource_helper.exists(bigip, name=payload['name'],
                                  partition=payload['partition']):
            if overwrite:
                LOG.debug("%s %s already exists ... updating",
                          resource_type, payload['name'])
                resource_helper.update(bigip, payload)
            else:
                LOG.debug("%s %s already exists, do not update.",
                          resource_type, payload['name'])
        else:
            LOG.debug("%s %s does not exist ... creating",
                      resource_type, payload['name'])
            resource_helper.create(bigip, payload)

    def _update(self, bigip, payload, old_resource, resource, service):
        if self.resource_helper.exists(bigip, name=payload['name'],
                                       partition=payload['partition']):
            LOG.debug("%s already exists ... updating", self._resource)
            self.resource_helper.update(bigip, payload)
        else:
            LOG.debug("%s does not exist ... creating", self._resource)
            payload = self._create_payload(resource, service)
            LOG.debug("%s payload is %s", self._resource, payload)
            self.resource_helper.create(bigip, payload)

    def _delete(self, bigip, payload, resource, service, **kwargs):
        resource_helper = kwargs.get("helper", self.resource_helper)
        resource_helper.delete(bigip, name=payload['name'],
                               partition=payload['partition'])

    def _check_update_needed(self, payload, old_resource, resource):
        if not payload or len(payload.keys()) == 0:
            return False
        return True

    @log_helpers.log_method_call
    def create(self, resource, service, **kwargs):
        if not service.get(self._key):
            self._search_element(resource, service)
        payload = kwargs.get("payload",
                             self._create_payload(resource, service))

        if not payload or len(payload.keys()) == 0:
            LOG.info("Do not need to create %s", self._resource)
            return

        if not payload.get("name") or not payload.get("partition"):
            create_payload = self._create_payload(resource, service)
            payload['name'] = create_payload['name']
            payload['partition'] = create_payload['partition']

        LOG.debug("%s payload is %s", self._resource, payload)
        bigips = self.driver.get_config_bigips(no_bigip_exception=True)
        for bigip in bigips:
            self._create(bigip, payload, resource, service)
        LOG.debug("Finish to create %s %s", self._resource, payload['name'])

    @log_helpers.log_method_call
    def update(self, old_resource, resource, service, **kwargs):
        if not service.get(self._key):
            self._search_element(resource, service)
        payload = kwargs.get("payload",
                             self._update_payload(old_resource, resource,
                                                  service))

        if self._check_update_needed(payload, old_resource, resource) is False:
            LOG.debug("Do not need to update %s", self._resource)
            return

        if not payload.get("name") or not payload.get("partition"):
            create_payload = self._create_payload(resource, service)
            payload['name'] = create_payload['name']
            payload['partition'] = create_payload['partition']

        LOG.debug("%s payload is %s", self._resource, payload)
        bigips = self.driver.get_config_bigips(no_bigip_exception=True)
        for bigip in bigips:
            self._update(bigip, payload, old_resource, resource, service)
        LOG.debug("Finish to update %s %s", self._resource, payload['name'])

    @log_helpers.log_method_call
    def delete(self, resource, service, **kwargs):
        if not service.get(self._key):
            self._search_element(resource, service)
        payload = kwargs.get("payload",
                             self._create_payload(resource, service))
        LOG.debug("%s payload is %s", self._resource, payload)
        bigips = self.driver.get_config_bigips(no_bigip_exception=True)
        for bigip in bigips:
            self._delete(bigip, payload, resource, service)
        LOG.debug("Finish to delete %s %s", self._resource, payload['name'])


class EndpointManager(ResourceManager):
    _collection_key = 'no_collection_key'
    _key = 'endpoint'

    def __init__(self, driver):
        super(EndpointManager, self).__init__(driver)
        self._resource = "virtual address"
