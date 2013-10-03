"""
Test to verify pagination for a list of groups.
"""
import unittest

from test_repo.autoscale.fixtures import AutoscaleFixture


class GroupPaginationTest(AutoscaleFixture):

    """
    Verify pagination for list group.
    """

    def setUp(self):
        """
        Create 10 scaling groups.
        """
        super(GroupPaginationTest, self).setUp()
        self._create_multiple_groups(4)
        self.total_groups = len(
            (self.autoscale_client.list_scaling_groups().entity).groups)

    def test_list_groups_with_limit_less_than_number_of_groups(self):
        """
        List the scaling groups with the limit set to be less than number of groups
        on the tenant and verify groups are listed in batches of the limit specified
        with a link for the next few groups.
        """
        param = self.total_groups - 1
        list_group = self._list_group_with_given_limit(param)
        self.assertEquals(len(list_group.groups), param,
                          msg='The length of the list when limited to {0} was {1} '
                          'instead'.format(param, len(list_group.groups)))
        rem_list_group = self.autoscale_client.list_scaling_groups(
            list_group.groups_links.next).entity
        self._assert_list_groups_with_limits(1, rem_list_group)

    @unittest.skip('fails')
    def test_list_groups_with_limit_equal_to_number_of_groups(self):
        """
        List the scaling groups with the limit set to be equal to the number of groups
        on the tenant and verify all the groups are listed without a link for the next
        few groups.
        """
        param = self.total_groups
        list_groups = self._list_group_with_given_limit(param)
        self._assert_list_groups_with_limits(param, list_groups)

    def test_list_groups_with_limit_greater_than_number_of_groups(self):
        """
        List the scaling groups with the limit set to be greater than the number of groups
        on the tenant and verify all the groups are listed without a link for the next
        few groups.
        """
        param = self.total_groups + 2
        list_groups = self._list_group_with_given_limit(param)
        self._assert_list_groups_with_limits(self.total_groups, list_groups)

    def test_list_groups_when_list_groups_is_greater_than_the_limit(self):
        """
        List the scaling groups without limit when over 100 groups exist on the group
        and verify the groups are listed in batches of the set limit with a next link.
        """
        self._create_multiple_groups(self.pagination_limit)
        list_groups = self._list_group_with_given_limit(None)
        self.assertEquals(len(list_groups.groups), self.pagination_limit)
        rem_list_group = self.autoscale_client.list_scaling_groups(
            list_groups.groups_links.next).entity
        self._assert_list_groups_with_limits(1, rem_list_group)

    @unittest.skip('PR-398')
    def test_list_groups_with_invalid_limits(self):
        """
        List scaling groups with limit set to invalid values and verify the message returned
        """
        params = [0, -1, 'ab', 10000]
        for each in params:
            self._list_group_with_given_limit(params, 400)

    def _list_group_with_given_limit(self, param, response=200):
        """
        Lists groups with given limit and verifies they are successfull
        """
        groups_response = self.autoscale_client.list_scaling_groups(limit=param)
        self.assertEquals(groups_response.status_code, response, msg='list group failed'
                          ' with {0}'.format(groups_response.status_code))
        return groups_response.entity

    def _assert_list_groups_with_limits(self, group_len, list_group):
        """
        Asserts the length of the list group returned and its groups links.
        """
        self.assertGreaterEqual(len(list_group.groups), group_len)
        self.assertDictEqual(list_group.groups_links.links, {}, msg='Links to next provided'
                             ' even when there are no more groups to list')

    def _create_multiple_groups(self, num):
        """
        Creates 'num' number of groups
        """
        for _ in range(num):
            group_response = self.autoscale_behaviors.create_scaling_group_min()
            self.group = group_response.entity
            self.resources.add(
                self.group.id, self.autoscale_client.delete_scaling_group)
