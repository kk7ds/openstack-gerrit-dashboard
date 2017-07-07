#!/usr/bin/env python

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

import argparse
import colorama
import json
import os
import pprint
import re
import sys
import time
import urllib2
import cStringIO
import gzip
import requests
import requests.auth


IGNORE_QUEUES = ['merge-check', 'silent']
CACHE = {}

session = requests.Session()


def make_filter(key, value, operator):
    if isinstance(value, list):
        return (' %s ' % operator).join(['%s:%s' % (key, _value)
                                         for _value in value])
    else:
        return '%s:%s' % (key, value)


def get_pending_changes(auth_creds, filters, operator, projects, gerrit_query):
    query_parts = []
    if filters:
        query_items = [make_filter(x, y, operator) for x, y in filters.items()]
        filters_query = '(' + (' %s ' % operator).join(query_items) + ')'
        query_parts.append(filters_query)

    if projects:
        projects = ['project:%s' % p for p in projects]
        project_query = '(' + ' OR '.join(projects) + ')'
        query_parts.append(project_query)

    if gerrit_query:
        query_parts.append(gerrit_query)

    query = '(%s)' % (' %s ' % operator).join(query_parts)
    if query.strip():
        query += ' AND '
    query += 'status:open'

    auth = requests.auth.HTTPDigestAuth(*auth_creds)
    result = session.get('https://review.openstack.org/a/changes/',
                         params={'q': query,
                                 'o': 'DETAILED_ACCOUNTS',
                                 'pp': '0'},
                         auth=auth,
                         timeout=30)
    result.raise_for_status()

    data = ''.join(x for x in result.iter_content(1024))
    result = data[5:]
    changes = json.loads(result)
    _changes = []
    for change in changes:
        if '_number' in change:
            change['number'] = change['_number']
        _changes.append(change)
    return _changes


def dump_gerrit(auth_creds, filters, operator, projects, query):
    pprint.pprint(get_pending_changes(auth_creds, filters, operator, projects, query))


def _get_zuul_status():
    req = urllib2.Request('http://zuul.openstack.org/status.json')
    req.add_header('Accept-encoding', 'gzip')
    zuul = urllib2.urlopen(req, timeout=60)
    data = ""
    while True:
        chunk = zuul.read()
        if not chunk:
            break
        data += chunk

    if zuul.info().get('Content-Encoding') == 'gzip':
        buf = cStringIO.StringIO(data)
        f = gzip.GzipFile(fileobj=buf)
        data = f.read()

    return json.loads(data)


def get_zuul_status():
    try:
        CACHE['zuul'] = _get_zuul_status()
        CACHE['zuul']['_retry'] = 0
    except Exception:
        try:
            CACHE['zuul']['_retry'] += 1
        except:
            pass
        pass
    return CACHE.get('zuul')


def dump_zuul():
    pprint.pprint(get_zuul_status())


def get_change_ids(changes):
    change_ids = {}
    for thing in changes:
        change_ids[int(thing[u'number'])] = {
            'subject': thing[u'subject'],
            'owner': thing[u'owner'],
            'starred': thing.get(u'starred'),
        }
    return change_ids


def is_dependent_queue(head):
    def find_pipeline(change):
        if ('jobs' in change and
            len(change['jobs']) > 0 and
                'pipeline' in change['jobs'][0]):
                return change['jobs'][0]['pipeline']
        return None

    pipelines = set(map(find_pipeline, head))
    return 'gate' in pipelines


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
    okay = None
    okay_statuses = ['SUCCESS']
    maybe_statuses = ['SKIPPED', 'ABORTED', 'CANCELED']
    status = ''
    for job in change['jobs']:
        total += 1
        if job['result']:
            complete += 1
            if job['voting']:
                if job['result'] in okay_statuses:
                    okay = 'yes' if okay is None else okay
                    status += '+'
                elif job['result'] in maybe_statuses:
                    okay = 'maybe' if okay != 'no' else okay
                    status += '?'
                else:
                    okay = 'no'
                    status += '-'
        else:
            if job['start_time']:
                status += '~'
            else:
                status += '_'
    if not total:
        return 0, '?', 'no'
    return (complete * 100) / total, status, okay


def process_changes(head, change_ids, queue_pos, queue_results):
    # with Depends-On we can have heads in independent pipelines, but
    # we should ignore everything except the last change in them
    # unless this is really a dependent pipeline.
    if len(head) > 0 and not is_dependent_queue(head):
        head = [head[-1]]

    for change in head:
        queue_pos += 1
        change_id = get_change_id(change)
        if change_id in change_ids:
            queue_results.append(
                {'pos': queue_pos,
                 'id': change['id'],
                 'subject': change_ids[change_id]['subject'],
                 'owner': change_ids[change_id]['owner'],
                 'starred': change_ids[change_id].get('starred'),
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
                    approval['by'].get('username') != 'jenkins'):
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
        if queue_name in IGNORE_QUEUES:
            continue
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


def yellow_line(line):
    return colorama.Fore.YELLOW + line + colorama.Fore.RESET


def red_line(line):
    return colorama.Fore.RED + line + colorama.Fore.RESET


def blue_line(line):
    return colorama.Fore.LIGHTBLUE_EX + line + colorama.Fore.RESET


def bright_line(line):
    return colorama.Style.BRIGHT + line + colorama.Style.RESET_ALL


def red_background_line(line):
    return (colorama.Back.RED + colorama.Style.BRIGHT + line +
            colorama.Style.RESET_ALL + colorama.Back.RESET)


def format_time(secs):
    if secs < 60:
        return "%is" % secs
    elif secs < 3600:
        return "%im" % (secs / 60)
    else:
        return "%ih%im" % ((secs / 3600),
                           (secs % 3600) / 60)


def calculate_time_in_queue(change):
    enqueue_timestamp = int(change['enqueue_time']) / 1000
    secs = time.time() - enqueue_timestamp
    return format_time(secs)


def calculate_time_remaining(change):
    enqueue_timestamp = int(change['enqueue_time']) / 1000
    secs = time.time() - enqueue_timestamp
    percent_done = change['status'][0]
    if percent_done != 0:
        total_time = int(float(secs) * 100. / float(percent_done))
        return format_time(total_time - secs)
    else:
        return '?m'


def error(msg):
    _reset_terminal()
    print red_background_line(msg)


def do_trigger_line(zuul_data):
    try:
        trigger_queue = zuul_data['trigger_event_queue']['length']
        msg = "Backlog: %i items" % trigger_queue
        if trigger_queue > 20:
            print red_background_line(msg)
        elif trigger_queue > 10:
            print yellow_line(msg)
        elif trigger_queue > 5:
            print msg
    except:
        pass

    try:
        retry = zuul_data['_retry']
        if retry > 0:
            print yellow_line('%i failed attempts' % retry)
    except:
        pass


def do_dashboard(auth_creds, user, filters, reset, show_jenkins, operator,
                 projects, query):
    try:
        changes = get_pending_changes(auth_creds, filters, operator, projects, query)
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
    do_trigger_line(zuul_data)
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
                time_remaining = calculate_time_remaining(change)
                percent, status, okay = change['status']
                line = '(%-8s) %s (%s/%s/rem:%s)' % (
                    change['id'],
                    change['subject'],
                    time_in_q,
                    status,
                    time_remaining)
                if queue == 'gate':
                    line = ('%3i: ' % change['pos']) + line
                else:
                    line = '     ' + line
                if change['owner'].get('username') == user:
                    if okay in ['yes', None]:
                        print green_line(line)
                    elif okay == 'maybe':
                        print yellow_line(line)
                    elif status == '?':
                        continue
                    else:
                        print red_line(line)
                elif change.get('starred'):
                    print blue_line(line)
                elif status != '?':
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


def osloconfig_parse(args, cfg):
    config_files = []
    path = os.environ.get('DASH_CONFIG_FILE', 'dash.conf')
    if os.path.exists(path):
        config_files.append(path)

    default_opts = []
    for opt in args:
        if opt.action in ('store_true', 'store_false'):
            o = cfg.BoolOpt(opt.dest,
                            short=opt._short_opts[0][1],
                            default=opt.default,
                            help=opt.help)
        elif opt.type == 'int':
            o = cfg.IntOpt(opt.dest,
                           short=opt._short_opts[0][1],
                           default=opt.default,
                           help=opt.help)
        elif opt.dest:
            o = cfg.StrOpt(opt.dest,
                           short=opt._short_opts[0][1],
                           default=opt.default,
                           help=opt.help)
        else:
            continue
        default_opts.append(o)

    conf = cfg.ConfigOpts()
    for opt in default_opts:
        conf.register_cli_opt(opt)
    conf(args, project='dash', default_config_files=config_files)
    return conf


def opt_parse(argv):
    usage = 'Usage: %s [options] [<username or review ID>]'
    argparser = argparse.ArgumentParser(usage=usage)
    argparser.add_argument('-u', '--user', help='Gerrit username',
                           default=os.environ.get('USER'))
    argparser.add_argument('-P', '--passwd', help='Gerrit password',
                           default=os.environ.get('PASS'))
    argparser.add_argument('-r', '--refresh', help='Refresh in seconds',
                           default=0, type=int)
    argparser.add_argument('-o', '--owner', default=None,
                           help='Show patches from this owner')
    argparser.add_argument('-c', '--change', default=None,
                           help='Show a particular patch set')
    argparser.add_argument('-p', '--projects', default='',
                           help='Comma separated list of projects')
    argparser.add_argument('-t', '--topic', default=None,
                           help='Show a particular topic only')
    argparser.add_argument('-q', '--query', default=None,
                           help='Use a specific gerrit query')
    argparser.add_argument('-w', '--watched', default=False,
                           action='store_true',
                           help='Show changes for all watched projects')
    argparser.add_argument('-s', '--starred', default=False,
                           action='store_true',
                           help='Show changes for all starred commits')
    argparser.add_argument('-O', '--operator', default='AND',
                           help='Join query elements with this operator '
                                '(OR or AND). The default is AND.')
    argparser.add_argument('-j', '--jenkins', default=False,
                           action='store_true',
                           help='Show jenkins scores for patches already '
                                'verified')
    argparser.add_argument('-Z', '--dump-zuul', help='Dump zuul data',
                           action='store_true', default=False)
    argparser.add_argument('-G', '--dump-gerrit', help='Dump gerrit data',
                           action='store_true', default=False)
    argparser.add_argument('username_or_review', help='username or review ID')
    return argparser.parse_args()


def parse_args(argv):
    args = opt_parse(argv)
    try:
        from oslo.config import cfg
        return osloconfig_parse(args, cfg)
    except ImportError:
        return args


def main():
    opts = parse_args(sys.argv)
    if opts.dump_zuul:
        dump_zuul()
        return

    auth_creds = (opts.user, opts.passwd)

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
    if not filters and not projects and not opts.query:
        filters = {'owner': opts.user}

    if opts.dump_gerrit:
        dump_gerrit(auth_creds, filters, opts.operator, projects, opts.query)
        return

    while True:
        try:
            do_dashboard(auth_creds, opts.user, filters, opts.refresh != 0,
                         opts.jenkins, opts.operator, projects, opts.query)
            if not opts.refresh:
                break
            time.sleep(opts.refresh)
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    main()
