#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

# This is a DOCUMENTATION stub specific to this module, it extends
# a documentation fragment located in ansible.utils.module_docs_fragments
DOCUMENTATION='''
module: rax_clb_ssl
short_description: Manage SSL termination for a Rackspace Cloud Load Balancer.
description:
- Set up, reconfigure, or remove SSL termination for an existing load balancer.
version_added: "2.0"
options:
  loadbalancer:
    description:
    - Name or ID of the load balancer on which to manage SSL termination.
    required: true
  state:
    description:
    - If set to "present", SSL termination will be added to this load balancer.
    - If "absent", SSL termination will be removed instead.
    choices:
      - present
      - absent
    default: present
  enabled:
    description:
    - If set to "false", temporarily disable SSL termination without discarding
    - existing credentials.
    default: true
  private_key:
    description:
    - The private SSL key as a string in PEM format.
  certificate:
    description:
    - The public SSL certificates as a string in PEM format.
  intermediate_certificate:
    description:
    - One or more intermediate certificate authorities as a string in PEM
    - format, concatenated into a single string.
  secure_port:
    description:
    - The port to listen for secure traffic.
    default: 443
  secure_traffic_only:
    description:
    - If "true", the load balancer will *only* accept secure traffic.
    default: false
  https_redirect:
    description:
    - If "true", the load balancer will redirect HTTP traffic to HTTPS.
    - Requires "secure_traffic_only" to be true. Incurs an implicit wait if SSL
    - termination is also applied or removed.
  wait:
    description:
    - Wait for the balancer to be in state "running" before turning.
    default: false
  wait_timeout:
    description:
    - How long before "wait" gives up, in seconds.
    default: 300
author: Ash Wilson
extends_documentation_fragment: rackspace
'''

EXAMPLES = '''
- name: Enable SSL termination on a load balancer
  rax_clb_ssl:
    balancer_name: the_loadbalancer
    state: present
    private_key: "{{ lookup('file', 'credentials/server.key' ) }}"
    certificate: "{{ lookup('file', 'credentials/server.crt' ) }}"
    intermediate_certificate: "{{ lookup('file', 'credentials/trust-chain.crt') }}"
    secure_traffic_only: true
    wait: true

- name: Disable SSL termination
  rax_clb_ssl:
    balancer_name: "{{ registered_lb.balancer.id }}"
    state: absent
    wait: true
'''

try:
    import pyrax
    HAS_PYRAX = True
except ImportError:
    HAS_PYRAX = False

def cloud_load_balancer_ssl(module, loadbalancer, state, enabled, private_key,
                            certificate, intermediate_certificate, secure_port,
                            secure_traffic_only, https_redirect,
                            wait, wait_timeout):
    # Validate arguments.

    if state == 'present':
        if not private_key:
            module.fail_json(msg="private_key must be provided.")
        else:
            private_key = private_key.strip()

        if not certificate:
            module.fail_json(msg="certificate must be provided.")
        else:
            certificate = certificate.strip()

    attempts = wait_timeout / 5

    # Locate the load balancer.

    balancer = rax_find_loadbalancer(module, pyrax, loadbalancer)
    existing_ssl = balancer.get_ssl_termination()

    changed = False

    if state == 'present':
        # Apply or reconfigure SSL termination on the load balancer.
        ssl_attrs = dict(
            securePort=secure_port,
            privatekey=private_key,
            certificate=certificate,
            intermediateCertificate=intermediate_certificate,
            enabled=enabled,
            secureTrafficOnly=secure_traffic_only
        )

        needs_change = False

        if existing_ssl:
            for ssl_attr, value in ssl_attrs.iteritems():
                if ssl_attr == 'privatekey':
                    # The private key is not included in get_ssl_termination's
                    # output (as it shouldn't be). Also, if you're changing the
                    # private key, you'll also be changing the certificate,
                    # so we don't lose anything by not checking it.
                    continue

                if value is not None and existing_ssl.get(ssl_attr) != value:
                    # module.fail_json(msg='Unnecessary change', attr=ssl_attr, value=value, existing=existing_ssl.get(ssl_attr))
                    needs_change = True
        else:
            needs_change = True

        if needs_change:
            balancer.add_ssl_termination(**ssl_attrs)
            changed = True
    elif state == 'absent':
        # Remove SSL termination if it's already configured.
        if existing_ssl:
            balancer.delete_ssl_termination()
            changed = True

    if https_redirect is not None and balancer.httpsRedirect != https_redirect:
        if changed:
            # This wait is unavoidable because load balancers are immutable
            # while the SSL termination changes above are being applied.
            pyrax.utils.wait_for_build(balancer, interval=5, attempts=attempts)

        balancer.update(httpsRedirect=https_redirect)
        changed = True

    if changed and wait:
        pyrax.utils.wait_for_build(balancer, interval=5, attempts=attempts)

    balancer.get()
    new_ssl_termination = balancer.get_ssl_termination()

    # Intentionally omit the private key from the module output, so you don't
    # accidentally echo it with `ansible-playbook -v` or `debug`, and the
    # certificate, which is just long. Convert other attributes to snake_case
    # and include https_redirect at the top-level.
    if new_ssl_termination:
        new_ssl = dict(
            enabled=new_ssl_termination['enabled'],
            secure_port=new_ssl_termination['securePort'],
            secure_traffic_only=new_ssl_termination['secureTrafficOnly']
        )
    else:
        new_ssl = None

    result = dict(
        changed=changed,
        https_redirect=balancer.httpsRedirect,
        ssl_termination=new_ssl,
        balancer=balancer
    )
    success = True

    if balancer.status == 'ERROR':
        result['msg'] = '%s failed to build' % balancer.id
        success = False
    elif wait and balancer.status not in ('ACTIVE', 'ERROR'):
        result['msg'] = 'Timeout waiting on %s' % balancer.id
        success = False

    if success:
        module.exit_json(**result)
    else:
        module.fail_json(**result)

def main():
    argument_spec = rax_argument_spec()
    argument_spec.update(dict(
        loadbalancer=dict(required=True),
        state=dict(default='present', choices=['present', 'absent']),
        enabled=dict(type='bool', default=True),
        private_key=dict(),
        certificate=dict(),
        intermediate_certificate=dict(),
        secure_port=dict(type='int', default=443),
        secure_traffic_only=dict(type='bool', default=False),
        https_redirect=dict(type='bool'),
        wait=dict(type='bool', default=False),
        wait_timeout=dict(type='int', default=300)
    ))

    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=rax_required_together(),
    )

    if not HAS_PYRAX:
        module.fail_json(msg='pyrax is required for this module.')

    loadbalancer = module.params.get('loadbalancer')
    state = module.params.get('state')
    enabled = module.boolean(module.params.get('enabled'))
    private_key = module.params.get('private_key')
    certificate = module.params.get('certificate')
    intermediate_certificate = module.params.get('intermediate_certificate')
    secure_port = module.params.get('secure_port')
    secure_traffic_only = module.boolean(module.params.get('secure_traffic_only'))
    https_redirect = module.boolean(module.params.get('https_redirect'))
    wait = module.boolean(module.params.get('wait'))
    wait_timeout = module.params.get('wait_timeout')

    setup_rax_module(module, pyrax)

    cloud_load_balancer_ssl(
        module, loadbalancer, state, enabled, private_key, certificate,
        intermediate_certificate, secure_port, secure_traffic_only,
        https_redirect, wait, wait_timeout
    )

from ansible.module_utils.basic import *
from ansible.module_utils.rax import *

main()
