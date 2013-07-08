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
import re
import sys
import time
import urllib
import getpass

def make_filter(key, value, operator):
    if isinstance(value, list):
        return (' %s ' % operator).join(['%s:%s' % (key, _value)
                                         for _value in value])
    else:
        return '%s:%s' % (key, value)


def get_pending_changes(client, filters, operator, projects):
    query_parts = []
    if filters:
        query_items = [make_filter(x, y, operator) for x, y in filters.items()]
        filters_query = '(' + (' %s ' % operator).join(query_items) + ')'
        query_parts.append(filters_query)

    if projects:
        projects = ['project:%s' % p for p in projects]
        project_query = '(' + ' OR '.join(projects) + ')'
        query_parts.append(project_query)

    query = '(%s)' % (' %s ' % operator).join(query_parts)
    if query.strip:
        query += ' AND '
    query += 'status:open --current-patch-set'

    cmd = 'gerrit query %s --format JSON' % query
    stdin, stdout, stderr = client.exec_command(cmd)
    changes = []
    for line in stdout:
        change = json.loads(line)
        if 'number' not in change:
            continue
        changes.append(change)
    return changes


def dump_gerrit(client, filters, operator, projects):
    pprint.pprint(get_pending_changes(client, filters, operator, projects))


def get_zuul_status():
    zuul = urllib.urlopen('http://zuul.openstack.org/status.json')
    return json.loads(zuul.read())


def dump_zuul():
    pprint.pprint(get_zuul_status())


def get_change_ids(changes):
    change_ids = {}
    for thing in changes:
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


def get_job_status(change):
    total = 0
    complete = 0
    okay = True
    for job in change['jobs']:
        total += 1
        if job['result']:
            complete += 1
            if (job['result'] != u'SUCCESS' and job['voting']):
                okay = False
    return (complete * 100) / total, okay


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
                 'enqueue_time': change['enqueue_time'],
                 'status': get_job_status(change),
                 })
    return queue_pos


def get_jenkins_info(changes):
    jenkins_info = []
    for change in changes:
        patch_set = change['currentPatchSet']
        change_id = '%s,%s' % (change['number'], patch_set['number'])
        for approval in patch_set.get('approvals', []):
            if (approval['type'] != 'VRIF' or
                approval['by']['username'] != 'jenkins'):
                continue
            score = approval['value']
            break
        else:
            score = '0'
        jenkins_info.append({'id': change_id,
                             'score': score,
                             'owner': change['owner'],
                             'subject': change['subject']})
    return jenkins_info


def find_changes_in_zuul(zuul_data, changes):
    change_ids = get_change_ids(changes)

    results = {}
    queue_stats = {}

    for queue in zuul_data['pipelines']:
        queue_name = queue['name']
        queue_pos = 0
        results[queue_name] = []
        for subq in queue['change_queues']:
            for head in subq['heads']:
                queue_pos = process_changes(head, change_ids,
                                            queue_pos,
                                            results[queue_name])
        queue_stats[queue_name] = queue_pos
    return results, queue_stats


def green_line(line):
    return colorama.Fore.GREEN + line + colorama.Fore.RESET


def red_line(line):
    return colorama.Fore.RED + line + colorama.Fore.RESET


def bright_line(line):
    return colorama.Style.BRIGHT + line + colorama.Style.RESET_ALL


def red_background_line(line):
    return colorama.Back.RED + line + colorama.Back.RESET


def calculate_time_in_queue(change):
    enqueue_timestamp = int(change['enqueue_time']) / 1000
    secs = time.time() - enqueue_timestamp
    if secs < 60:
        return "%is" % secs
    elif secs < 3600:
        return "%im" % (secs / 60)
    else:
        return "%ih%im" % ((secs / 3600),
                           (secs % 3600) / 60)

def error(msg):
    _reset_terminal()
    print red_background_line(msg)


def do_dashboard(client, user, filters, reset, show_jenkins, operator, projects):
    try:
        changes = get_pending_changes(client, filters, operator, projects)
    except paramiko.ssh_exception.SSHException:
        raise
    except Exception as e:
        error('Failed to get changes from Gerrit: %s' % e)
        return
    try:
        zuul_data = get_zuul_status()
        results, queue_stats = find_changes_in_zuul(zuul_data, changes)
    except Exception as e:
        error('Failed to get data from Zuul: %s' % e)
        return

    if reset:
        reset_terminal(filters, operator, projects)
    if u'message' in zuul_data:
        msg = re.sub('<[^>]+>', '', zuul_data['message'])
        print red_background_line('Zuul: %s' % msg)
    change_ids_not_found = get_change_ids(changes).keys()
    for queue, zuul_info in results.items():
        if zuul_info:
            print bright_line("Queue: %s (%i/%i)" % (queue, len(zuul_info),
                                                     queue_stats[queue]))
            for change in zuul_info:
                change_id = get_change_id(change)
                if change_id in change_ids_not_found:
                    change_ids_not_found.remove(change_id)
                time_in_q = calculate_time_in_queue(change)
                status, okay = change['status']
                line = '(%-8s) %s (%s/%02i%%)' % (change['id'],
                                                  change['subject'],
                                                  time_in_q,
                                                  status)
                if queue == 'gate':
                    line = ('%3i: ' % change['pos']) + line
                else:
                    line = '     ' + line
                if change['owner']['username'] == user:
                    if okay:
                        print green_line(line)
                    else:
                        print red_line(line)
                else:
                    print line
    # Show info about changes not in zuul.
    if show_jenkins and change_ids_not_found:
        print "Jenkins scores:"
        changes_not_found = [x for x in changes
                if int(x['number']) in change_ids_not_found]
        jenkins_info = get_jenkins_info(changes_not_found)
        for info in jenkins_info:
            line = " %2s: (%-8s) %s" % (info['score'], info['id'],
                                        info['subject'])
            if info['owner']['username'] == user:
                print green_line(line)
            else:
                print line

def _reset_terminal():
    sys.stderr.write("\x1b[2J\x1b[H")


def reset_terminal(filters, operator, projects):
    if operator == 'OR':
        delim = '+'
    else:
        delim = ','
    _reset_terminal()
    target = delim.join('%s:%s' % (x, y) for x, y in filters.items())
    print "Dashboard for %s %s - %s " % (target, projects, time.asctime())


def connect_client(opts):
    connect_args = {
        'port': 29418,
        'username': opts.user,
        'key_filename': opts.ssh_key
    }
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.load_system_host_keys()
    try:
        client.connect('review.openstack.org', **connect_args)
    except paramiko.PasswordRequiredException:
        print "SSH key is encrypted. Asking for the passphrase..."
        ssh_key_pw = getpass.getpass()
        connect_args['password'] = ssh_key_pw
        try:
            client.connect('review.openstack.org', **connect_args)
        except:
            client = None
    except:
        client = None
    return client

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
    optparser.add_option('-p', '--projects', default='',
                         help='Comma separated list of projects')
    optparser.add_option('-t', '--topic', default=None,
                         help='Show a particular topic only')
    optparser.add_option('-w', '--watched', default=False,
                         action='store_true',
                         help='Show changes for all watched projects')
    optparser.add_option('-s', '--starred', default=False,
                         action='store_true',
                         help='Show changes for all starred commits')
    optparser.add_option('-O', '--operator', default='AND',
                         help='Join query elements with this operator')
    optparser.add_option('-j', '--jenkins', default=False,
                         action='store_true',
                         help='Show jenkins scores for patches already '
                              'verified')
    optparser.add_option('-Z', '--dump-zuul', help='Dump zuul data',
                         action='store_true', default=False)
    optparser.add_option('-G', '--dump-gerrit', help='Dump gerrit data',
                         action='store_true', default=False)
    opts, args = optparser.parse_args()

    if opts.dump_zuul:
        dump_zuul()
        return

    client = connect_client(opts)

    filters = {}
    for filter_key in ['owner', 'change', 'topic']:
        value = getattr(opts, filter_key)
        if value is None:
            continue
        filters[filter_key] = value

    projects = opts.projects.split(',') if opts.projects else []

    if opts.watched or opts.starred:
        filters['is'] = []
    if opts.watched:
        filters['is'].append('watched')
    if opts.starred:
        filters['is'].append('starred')

    # Default case
    if not filters and not projects:
        filters = {'owner': opts.user}

    if opts.dump_gerrit:
        dump_gerrit(client, filters, opts.operator, projects)
        return

    while True:
        try:
            try:
                do_dashboard(client, opts.user, filters, opts.refresh != 0,
                             opts.jenkins, opts.operator, projects)
            except paramiko.ssh_exception.SSHException:
                error('Reconnecting to Gerrit...')
                client = connect_client(opts)
            if not opts.refresh:
                break
            time.sleep(opts.refresh)
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    main()
