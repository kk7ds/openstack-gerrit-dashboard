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

import colorama
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
    query += ' '.join('%s:%s' % (x, y) for x, y in filters.items())
    cmd = 'gerrit query "%s" --format JSON' % query
    stdin, stdout, stderr = client.exec_command(cmd)
    changes = []
    for line in stdout:
        changes.append(json.loads(line))
    return changes


def dump_gerrit(client, filters):
    pprint.pprint(get_pending_changes(client, filters))


def get_zuul_status():
    zuul = urllib.urlopen('http://zuul.openstack.org/status.json')
    return json.loads(zuul.read())


def dump_zuul():
    pprint.pprint(get_zuul_status())


def get_change_ids(changes):
    change_ids = {}
    for thing in changes:
        if u'number' in thing:
            change_ids[int(thing[u'number'])] = {
                'subject': thing[u'subject'],
                'owner': thing[u'owner'],
                }
    return change_ids


def get_change_id(change):
    try:
        change_id = int(change['id'].split(',')[0])
    except:
        # Dunno what this is
        return False

    return change_id


def process_changes(head, change_ids, queue_pos, queue_results):
    for change in head:
        queue_pos += 1
        change_id = get_change_id(change)
        if change_id in change_ids:
            queue_results.append(
                {'pos': queue_pos,
                 'id': change['id'],
                 'subject': change_ids[change_id]['subject'],
                 'owner': change_ids[change_id]['owner'],
                 })
    return queue_pos

def find_changes_in_zuul(zuul_data, changes):
    change_ids = get_change_ids(changes)

    results = {}

    for queue in zuul_data['pipelines']:
        queue_name = queue['name']
        queue_pos = 0
        results[queue_name] = []
        for subq in queue['change_queues']:
            for head in subq['heads']:
                queue_pos = process_changes(head, change_ids,
                                            queue_pos,
                                            results[queue_name])
    return results


def green_line(line):
    return colorama.Fore.GREEN + line + colorama.Fore.RESET


def do_dashboard(client, user, filters, reset):
    changes = get_pending_changes(client, filters)
    zuul_data = get_zuul_status()
    results = find_changes_in_zuul(zuul_data, changes)
    if reset:
        reset_terminal(filters)
    for queue, changes in results.items():
        if changes:
            print "Queue: %s" % queue
            for change in changes:
                line = " %3i: (%-8s) %s" % (change['pos'], change['id'],
                                            change['subject'])
                if change['owner']['username'] == user:
                    print green_line(line)
                else:
                    print line


def reset_terminal(filters):
    sys.stderr.write("\x1b[2J\x1b[H")
    target = ','.join('%s:%s' % (x, y) for x, y in filters.items())
    print "Dashboard for %s - %s " % (target, time.asctime())


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
    optparser.add_option('-c', '--change', default=None,
                         help='Show a particular patch set')
    optparser.add_option('-p', '--project', default=None,
                         help='Show a particular project only')
    optparser.add_option('-Z', '--dump-zuul', help='Dump zuul data',
                         action='store_true', default=False)
    optparser.add_option('-G', '--dump-gerrit', help='Dump gerrit data',
                         action='store_true', default=False)
    opts, args = optparser.parse_args()

    if opts.dump_zuul:
        dump_zuul()
        return

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.load_system_host_keys()
    client.connect('review.openstack.org', port=29418, username=opts.user,
                   key_filename=opts.ssh_key)

    filters = {}
    for filter_key in ['owner', 'change', 'project']:
        value = getattr(opts, filter_key)
        if value is None:
            continue
        filters[filter_key] = value

    # Default case
    if not filters:
        filters = {'owner': opts.user}

    if opts.dump_gerrit:
        dump_gerrit(client, filters)
        return

    while True:
        try:
            do_dashboard(client, opts.user, filters, opts.refresh != 0)
            if not opts.refresh:
                break
            time.sleep(opts.refresh)
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    main()
