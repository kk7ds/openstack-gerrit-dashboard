import unittest

import mox
import paramiko

import dash


class TestDash(unittest.TestCase):
    def setUp(self):
        super(TestDash, self).setUp()
        self.mox = mox.Mox()

    def test_make_filter(self):
        result = dash.make_filter('foo', 'bar', 'MYOP')
        self.assertEqual('foo:bar', result)

    def test_make_filter_list(self):
        result = dash.make_filter('foo', ['bar', 'baz'], 'MYOP')
        self.assertEqual('foo:bar MYOP foo:baz', result)

    def test_get_job_status_okay(self):
        change = {'jobs':
                      [{'result': 'SUCCESS', 'voting': True},
                       {'result': None, 'voting': True},
                       {'result': 'FAILED', 'voting': False},
                       ]}

        complete, okay = dash.get_job_status(change)
        self.assertEqual(66, complete)
        self.assertEqual('yes', okay)

    def test_get_job_status_notokay(self):

        change = {'jobs':
                      [{'result': 'SUCCESS', 'voting': True},
                       {'result': None, 'voting': True},
                       {'result': 'FAILED', 'voting': True},
                       ]}

        complete, okay = dash.get_job_status(change)
        self.assertEqual(66, complete)
        self.assertEqual('no', okay)

    def test_get_job_status_maybe(self):

        change = {'jobs':
                      [{'result': 'SUCCESS', 'voting': True},
                       {'result': None, 'voting': True},
                       {'result': 'ABORTED', 'voting': True},
                       ]}

        complete, okay = dash.get_job_status(change)
        self.assertEqual(66, complete)
        self.assertEqual('maybe', okay)

    def test_get_job_status_no_jobs(self):

        change = {'jobs': []}

        complete, okay = dash.get_job_status(change)
        self.assertEqual(0, complete)
        self.assertEqual(None, okay)

    def test_get_change_id(self):
        self.assertEqual(1234, dash.get_change_id({'id': '1234,10'}))

    def test_change_ids(self):
        changes = [
            {u'number': 123, u'subject': 'foo', u'owner': 'dan'},
            {u'number': 456, u'subject': 'bar', u'owner': 'dan'},
            ]
        result = dash.get_change_ids(changes)
        self.assertEqual({123: {'subject': 'foo', 'owner': 'dan'},
                          456: {'subject': 'bar', 'owner': 'dan'},
                          }, result)

    def _test_gerrit_query(self, query, filters, operator, projects):
        client = self.mox.CreateMockAnything()
        query = query + ' --current-patch-set'
        client.exec_command('gerrit query %s --format JSON' % query
                            ).AndReturn((mox.IgnoreArg(),
                                         [],
                                         mox.IgnoreArg()))
        self.mox.ReplayAll()
        dash.get_pending_changes(client, filters, operator, projects)

    def test_get_pending_changes_with_filters(self):
        self._test_gerrit_query('((owner:foo)) AND status:open',
                                {'owner': 'foo'}, 'AND', [])

    def test_get_pending_changes_with_filters_OR(self):
        self._test_gerrit_query('((owner:foo)) AND status:open',
                                {'owner': 'foo'}, 'OR', [])

    def test_get_pending_changes_with_project(self):
        self._test_gerrit_query('((project:foo)) AND status:open',
                                {}, 'AND', ['foo'])

    def test_get_pending_changes_with_projects(self):
        self._test_gerrit_query(
            '((project:foo OR project:bar)) AND status:open',
            {}, 'AND', ['foo', 'bar'])

    def test_get_pending_changes_with_projects_and_filters(self):
        self._test_gerrit_query(
            '((owner:baz) AND (project:foo OR project:bar)) AND status:open',
            {'owner': 'baz'}, 'AND', ['foo', 'bar'])

    def test_get_pending_changes_with_projects_and_filters_OR(self):
        self._test_gerrit_query(
            '((owner:baz) OR (project:foo OR project:bar)) AND status:open',
            {'owner': 'baz'}, 'OR', ['foo', 'bar'])

    def test_get_pending_changes_with_multiple_values(self):
        self._test_gerrit_query(
            '((is:starred AND is:watched)) AND status:open',
            {'is': ['starred', 'watched']}, 'AND', [])

    def test_gerrit_reconnect(self):
        class FakeOpts(object):
            projects = None
            owner = None
            change = None
            topic = None
            watched = None
            starred = None
            user = None
            refresh = 1
            jenkins = False
            operator = 'OR'
            dump_zuul = False
            dump_gerrit = False

        fake_opts = FakeOpts()
        self.mox.StubOutWithMock(dash, 'parse_args')
        self.mox.StubOutWithMock(dash, 'connect_client')
        self.mox.StubOutWithMock(dash, 'do_dashboard')
        self.mox.StubOutWithMock(dash, 'error')
        dash.error(mox.IgnoreArg()).MultipleTimes()
        dash.parse_args(mox.IgnoreArg()).AndReturn(fake_opts)
        dash.connect_client(fake_opts).AndReturn('client')

        # Client is broken, do_dashboard throws an error
        dash.do_dashboard(
            'client', None, mox.IgnoreArg(), True, False, 'OR', []
            ).AndRaise(paramiko.ssh_exception.SSHException())

        # We try to reconnect and fail
        dash.connect_client(fake_opts).AndReturn(None)

        # Make sure do_dashboard is called with the old client
        dash.do_dashboard(
            'client', None, mox.IgnoreArg(), True, False, 'OR', []
            ).AndRaise(paramiko.ssh_exception.SSHException())

        # We try to reconnect and succeed this time
        dash.connect_client(fake_opts).AndReturn('new-client')

        # Make sure do_dashboard is called with the new client
        dash.do_dashboard('new-client', None, mox.IgnoreArg(),
                          True, False, 'OR', [])
        
        # It will be called one more time because of refresh
        dash.do_dashboard('new-client', None, mox.IgnoreArg(),
                          True, False, 'OR', []).AndRaise(
                              KeyboardInterrupt)

        self.mox.ReplayAll()

        dash.main()
        
        self.mox.VerifyAll()
        

if __name__ == '__main__':
    unittest.main()
