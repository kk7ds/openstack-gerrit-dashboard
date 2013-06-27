import unittest

import mox

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
        self.assertTrue(okay)

    def test_get_job_status_notokay(self):

        change = {'jobs':
                      [{'result': 'SUCCESS', 'voting': True},
                       {'result': None, 'voting': True},
                       {'result': 'FAILED', 'voting': True},
                       ]}

        complete, okay = dash.get_job_status(change)
        self.assertEqual(66, complete)
        self.assertFalse(okay)

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
