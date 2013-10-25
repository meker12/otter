"""
The OtterClock.  Because, while a broken clock is right twice a day, an OtterClock
is right all the time and is probably what caused your regular clock to get broken
in the first place.
"""

from datetime import datetime
from functools import partial
from croniter import croniter

from twisted.internet import defer
from twisted.application.internet import TimerService

from otter.util.hashkey import generate_transaction_id
from otter.controller import maybe_execute_scaling_policy, CannotExecutePolicyError
from otter.log import log as otter_log
from otter.models.interface import NoSuchPolicyError, NoSuchScalingGroupError
from otter.util.deferredutils import ignore_and_log


def next_cron_occurrence(cron):
    """
    Return next occurence of given cron entry
    """
    return croniter(cron, start_time=datetime.utcnow()).get_next(ret_type=datetime)


class RoundRobinItems(object):
    """
    List of items that will give next item from the list in round robin fashion
    """

    def __init__(self, items):
        self.items = items
        self._index = 0

    def next_item(self):
        """
        Return next item
        """
        self._index = (self._index + 1) % len(items)
        return self.items[self._index]


class SchedulerService(TimerService):
    """
    Service to trigger scheduled events
    """

    def __init__(self, batchsize, interval, slv_client, store, zk_hosts,
                 zk_partition_path, clock=None):
        """
        Initializes the scheduler service with batch size and interval

        :param int batchsize: number of events to fetch on each iteration
        :param int interval: time between each iteration
        :param slv_client: a :class:`silverberg.client.CQLClient` or
                    :class:`silverberg.cluster.RoundRobinCassandraCluster` instance used to get lock
        :param clock: An instance of IReactorTime provider that defaults to reactor if not provided
        """
        TimerService.__init__(self, interval, self.check_for_events, batchsize)
        self.store = store
        self.clock = clock
        self.zk_host = zk_host
        self.zk_partition_path = zk_partition_path
        self.kz_client, self.kz_partition = None, None
        self.log = otter_log.bind(system='otter.scheduler')

    def startService(self):
        """
        Start this service. This will start partitoning
        """
        super(SchedulerService, self).startService()
        self.kz_client = TxKazooClient(hosts=self.zk_hosts)
        d = self.kz_client.start()
        d.addCallback(self.start_partitioner)

    def start_partitioner(self, _):
        self.kz_partition = self.kz_client.SetPartition(self.zk_partition_path)

    def check_for_events(self, batchsize):
        """
        Check for events occurring now and earlier
        """
        if not self.kz_partition or self.kz_partition.allocating:
            self.log.msg('Partition allocating')
            return
        if self.kz_partition.release:
            self.log.msg('Partition changed. Repartitioning')
            return self.kz_partition.release_set()
        if self.kz_partition.failed:
            self.log.msg('Partition failed. Starting new')
            self.start_partitioner(None)
            return

        buckets = list(self.kz_partition)
        utcnow = datetime.utcnow()

        return defer.gatherResults(
            [self.check_for_events_in_bucket(bucket, now, batchsize) for bucket in buckets])

    def check_for_events_in_bucket(self, bucket, now, batchsize):
        """
        Check for events in the given bucket before `now`

        :return: a deferred that fires with None
        """

        def check_for_more(events):
            if events and len(events) == batchsize:
                return _do_check()
            return None

        def _do_check():
            d = self.store.fetch_and_delete(now, batchsize)
            d.addCallback(self.process_events, now)
            d.addCallback(check_for_more)
            d.addErrback(self.log.err)
            return d

        return _do_check()

    def process_events(self, events, now):
        """
        Fetch the events to be processed and process them.
        Also delete/update after processing them

        :return: a deferred that fires with list of events processed
        """
        if not events:
            return events

        log = self.log.bind(scheduler_run_id=generate_transaction_id(), utcnow=now)
        log.msg('Processing {num_events} events', num_events=len(events))

        deleted_policy_ids = set()

        deferreds = [
            self.execute_event(log, event, deleted_policy_ids)
            for event in events
        ]
        d = defer.gatherResults(deferreds, consumeErrors=True)
        return d.addCallback(lambda _: self.add_cron_events(log, events, deleted_policy_ids))

    def add_cron_events(self, log, events, deleted_policy_ids):
        """
        Update events with cron entry with next trigger time
        """
        if not events:
            return events

        new_cron_event = []
        for event in events:
            if event['cron'] and event['policyId'] not in deleted_policy_ids:
                event['trigger'] = next_cron_occurrence(event['cron'])
                new_cron_event.append(event)

        log.msg('Adding {new_cron_events} cron events', new_cron_events=len(new_cron_events))
        d = self.store.add_cron_events(new_cron_events)
        return d.addCallback(lambda _: events)

    def execute_event(self, log, event, deleted_policy_ids):
        """
        Execute a single event

        :param log: A bound log for logging
        :param event: event dict to execute
        :param deleted_policy_ids: Set of policy ids that are deleted. Policy id will be added
                                   to this if its scaling group or policy has been deleted
        :return: a deferred with the results of execution
        """
        tenant_id, group_id, policy_id = event['tenantId'], event['groupId'], event['policyId']
        log = log.bind(tenant_id=tenant_id, scaling_group_id=group_id, policy_id=policy_id)
        log.msg('Executing policy')
        group = self.store.get_scaling_group(log, tenant_id, group_id)
        d = group.modify_state(partial(maybe_execute_scaling_policy,
                                       log, generate_transaction_id(),
                                       policy_id=policy_id))
        d.addErrback(ignore_and_log, CannotExecutePolicyError, log, 'Cannot execute policy')

        def collect_deleted_policy(failure):
            failure.trap(NoSuchScalingGroupError, NoSuchPolicyError)
            deleted_policy_ids.add(policy_id)

        d.addErrback(collect_deleted_policy)
        d.addErrback(log.err, 'Scheduler failed to execute policy')
        return d
