#!/usr/bin/python

#    Copyright 2013 IBM Corp.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import optparse
import os
import paramiko
import pprint
import sys
import time
import urllib


def get_pending_changes(client, filters):
    query = 'status:open '
    if filters['patch']:
        query += 'change:%s' % filters['patch']
    else:
        query += 'owner:%s' % filters['owner']
    print query
    cmd = 'gerrit query "%s" --format JSON' % query
    stdin, stdout, stderr = client.exec_command(cmd)
    changes = []
    for line in stdout:
        changes.append(json.loads(line))
    return changes


def get_zuul_status():
    zuul = urllib.urlopen('http://zuul.openstack.org/status.json')
    return json.loads(zuul.read())


def dump_zuul():
    pprint.pprint(get_zuul_status())


def get_my_change_ids(my_changes):
    my_change_ids = {}
    for thing in my_changes:
        if u'number' in thing:
            my_change_ids[int(thing[u'number'])] = thing[u'subject']
    return my_change_ids


def get_change_id(change):
    try:
        change_id = int(change['id'].split(',')[0])
    except:
        # Dunno what this is
        return False

    return change_id


def find_my_changes(zuul_data, my_changes):
    my_change_ids = get_my_change_ids(my_changes)

    results = {}

    for queue in zuul_data['pipelines']:
        queue_name = queue['name']
        queue_pos = 0
        results[queue_name] = []
        for subq in queue['change_queues']:
            for head in subq['heads']:
                for change in head:
                    queue_pos += 1
                    change_id = get_change_id(change)
                    if change_id in my_change_ids:
                        results[queue_name].append(
                            {'pos': queue_pos,
                             'id': change['id'],
                             'subject': my_change_ids[change_id],
                             })
    return results


def do_dashboard(client, filters, reset):
    my_changes = get_pending_changes(client, filters)
    zuul_data = get_zuul_status()
    results = find_my_changes(zuul_data, my_changes)
    if reset:
        reset_terminal(filters['patch'] or filters['owner'])
    for queue, changes in results.items():
        if changes:
            print "Queue: %s" % queue
            for change in changes:
                print " %3i: (%-8s) %s" % (change['pos'], change['id'],
                                         change['subject'])


def reset_terminal(owner):
    sys.stderr.write("\x1b[2J\x1b[H")
    print "Dashboard for %s - %s " % (owner, time.asctime())


def main():
    usage = 'Usage: %s [options] [<username or review ID>]'
    optparser = optparse.OptionParser(usage=usage)
    optparser.add_option('-u', '--user', help='Gerrit username',
                         default=os.environ.get('USER'))
    optparser.add_option('-r', '--refresh', help='Refresh in seconds',
                         default=0, type=int)
    optparser.add_option('-k', '--ssh_key', default=None,
                         help='SSH key to use for gerrit')
    optparser.add_option('-o', '--owner', default=None,
                         help='Show patches from this owner')
    optparser.add_option('-p', '--patch', default=None,
                         help='Show a particular patch set')
    optparser.add_option('-Z', '--dump-zuul', help='Dump zuul data',
                         action='store_true', default=False)
    opts, args = optparser.parse_args()

    if opts.dump_zuul:
        dump_zuul()
        sys.exit()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.load_system_host_keys()
    client.connect('review.openstack.org', port=29418, username=opts.user,
                   key_filename=opts.ssh_key)

    filters = {'owner': opts.owner or opts.user,
               'patch': opts.patch,
              }

    while True:
        try:
            do_dashboard(client, filters, opts.refresh != 0)
            if not opts.refresh:
                break
            time.sleep(opts.refresh)
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    main()
