"""
Data classes for representing bits of information that need to share a
representation across the different phases of convergence.
"""

from characteristic import attributes, Attribute

from pyrsistent import freeze

from twisted.python.constants import Names, NamedConstant

from zope.interface import implementer, Interface
from zope.interface import Attribute as IAttribute


class CLBNodeCondition(Names):
    """Constants representing the condition a load balancer node can be in"""
    ENABLED = NamedConstant()   # Node can accept new connections.
    DRAINING = NamedConstant()  # Node cannot accept any new connections.
                                # Existing connections are forcibly terminated.
    DISABLED = NamedConstant()  # Node cannot accept any new connections.
                                # Existing connections are permitted to continue.


class CLBNodeType(Names):
    """Constants representing the type of a load balancer node"""
    PRIMARY = NamedConstant()    # Node in normal rotation
    SECONDARY = NamedConstant()  # Node only put into normal rotation if a
                                 # primary node fails.


class ServerState(Names):
    """Constants representing the state cloud servers can have"""
    ACTIVE = NamedConstant()    # corresponds to Nova "ACTIVE"
    ERROR = NamedConstant()     # corresponds to Nova "ERROR"
    BUILD = NamedConstant()     # corresponds to Nova "BUILD" or "BUILDING"
    DRAINING = NamedConstant()  # Autoscale is deleting the server


@attributes(['id', 'state', 'created',
             Attribute('servicenet_address', default_value='', instance_of=str)])
class NovaServer(object):
    """
    Information about a server that was retrieved from Nova.

    :ivar str id: The server id.
    :ivar str state: Current state of the server.
    :ivar float created: Timestamp at which the server was created.
    :ivar str servicenet_address: The private ServiceNet IPv4 address, if
        the server is on the ServiceNet network
    """


@attributes(['launch_config', 'desired',
             Attribute('desired_lbs', default_value=(), instance_of=tuple),
             Attribute('draining_timeout', default_value=0.0, instance_of=float)])
class DesiredGroupState(object):
    """
    The desired state for a scaling group.

    :ivar dict launch_config: nova launch config.
    :ivar int desired: the number of desired servers within the group.
    :ivar tuple desired_lbs: A tuple of :class:`ILBDescription` providers.
    :ivar float draining_timeout: If greater than zero, when the server is
        scaled down it will be put into draining condition.  It will remain
        in draining condition for a maximum of ``draining_timeout`` seconds
        before being removed from the load balancer and then deleted.
    """
    def __init__(self):
        """
        Make attributes immutable.
        """
        self.launch_config = freeze(self.launch_config)


class ILBDescription(Interface):
    """
    A description of how to create a node on a load balancing entity.

    A load balancing entity can be a cloud load balancer or some kind of load
    balancer pool - anything that load balances.

    Implementers should have immutable attributes.
    """
    def equivalent_definition(other_description):
        """
        Checks whether two description have the same definitions.

        A definition is anything non-server specific information that describes
        how to add a node to a particular load balancing entity.  For instance,
        the type of load balancer, the load balancer ID, and/or the port.

        :param ILBDescription other_description: the other description to
            compare against
        :return: whether the definitions are equivalent
        :rtype: `bool`
        """


class ILBNode(Interface):
    """
    A node, which is a mapping between a server and a :class:`ILBDescription`.

    :ivar ILBDescription description: The description of how the server is
        mapped to the load balancer.
    :ivar str node_id: The ID of the node, which is represents a unique
        mapping of a server to a load balancer (possibly one of many).
    :ivar NovaServer: The server corresponding to this node.
    """
    server = IAttribute("The server that corresponds to this node.")
    node_id = IAttribute("The ID of this node.")
    description = IAttribute("The LB Description for how this server is "
                             "attached to the load balancer.")


class IDrainable(Interface):
    """
    The drainability part of a LB Node.  If a node is drainable, it should
    also provide this interface.
    """
    def currently_draining():
        """
        Is this node currently in draining mode?
        """

    def is_done_draining(now, timeout):
        """
        Given the current time and the draining timeout, can this node be
        done draining?
        """


@implementer(ILBDescription)
@attributes([Attribute("lb_id", instance_of=str),
             Attribute("port", instance_of=int),
             Attribute("weight", default_value=1, instance_of=int),
             Attribute("condition", default_value=CLBNodeCondition.ENABLED,
                       instance_of=NamedConstant),
             Attribute("type", default_value=CLBNodeType.PRIMARY,
                       instance_of=NamedConstant)])
class CLBDescription(object):
    """
    Information representing a Rackspace CLB port mapping; how a particular
    server *should* be port-mapped to a Rackspace Cloud Load Balancer.

    :ivar int lb_id: The Load Balancer ID.
    :ivar int port: The port, which together with the server's IP, specifies
        the service that should be load-balanced by the load balancer.
    :ivar int weight: The weight to be used for certain load-balancing
        algorithms if configured on the load balancer.  Defaults to 1,
        the max is 100.
    :ivar str condition: One of ``ENABLED``, ``DISABLED``, or ``DRAINING`` -
        the default is ``ENABLED``
    :ivar str type: One of ``PRIMARY`` or ``SECONDARY`` - default is ``PRIMARY``
    """
    def equivalent_definition(self, other_description):
        """
        Whether the other description is also a :class:`CLBDescription` and
        whether it has the same load balancer ID and port.

        See :func:`ILBDescription.equivalent_definition`.
        """
        return (isinstance(other_description, CLBDescription) and
                other_description.lb_id == self.lb_id and
                other_description.port == self.port)


@implementer(ILBDescription)
@attributes([Attribute("pool_id", instance_of=str)])
class RCv3Description(object):
    """
    Information representing a Rackspace RackConnect v3 load balancer.

    :ivar int lb_id: The Load Balancer Pool ID.
    """
    def equivalent_definition(self, other_description):
        """
        Whether the other description is also a :class:`RCv3Description` and
        whether it has the same load balancer pool ID.

        See :func:`ILBDescription.equivalent_definition`.
        """
        return (isinstance(other_description, RCv3Description) and
                other_description.pool_id == self.pool_id)
