# Copyright (c) 2021, F5 Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import f5_endpoint_agent
import setuptools

setuptools.setup(
    version=f5_endpoint_agent.__version__,
    name="f5-endpoint-agent",
    description = ("F5 Networks Endpoint Agent for OpenStack services"),
    license = 'Apache License, Version 2.0',
    author="F5 Networks",
    author_email="f5_endpoint_agent@f5.com",
    data_files=[('/etc/neutron/services/f5', ['etc/neutron/services/f5/f5-endpoint-agent.ini']),
                ('/etc/init.d', ['etc/init.d/f5-endpoint-agent']),
                ('/usr/lib/systemd/system', ['lib/systemd/system/f5-endpoint-agent.service'])
                # ('/usr/bin/f5', ['bin/debug_bundler.py'])
                ],
    packages=setuptools.find_packages(exclude=['*.test', '*.test.*', 'test*', 'test']),
    classifiers=[
        'Environment :: OpenStack',
	'Intended Audience :: Information Technology',
	'Intended Audience :: System Administrators',
	'License :: OSI Approved :: Apache Software License',
	'Operating System :: POSIX :: Linux',
	'Programming Language :: Python',
	'Programming Language :: Python :: 2',
	'Programming Language :: Python :: 2.7'
    ],
    entry_points={
        'console_scripts': [
            'f5-endpoint-agent = f5_endpoint_agent.endpoint.drivers.bigip.agent:main'
        ]
    },
    install_requires=['f5-sdk==3.0.11']
)