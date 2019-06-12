##############################################################################
#
# Copyright (c) 2008 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""PostgreSQL adapter for RelStorage."""
from __future__ import absolute_import

import logging

from perfmetrics import metricmethod

from ..connmanager import AbstractConnectionManager

log = logging.getLogger(__name__)


class Psycopg2ConnectionManager(AbstractConnectionManager):

    def __init__(self, driver, dsn, options):
        self._dsn = dsn
        self.disconnected_exceptions = driver.disconnected_exceptions
        self.close_exceptions = driver.close_exceptions
        self.use_replica_exceptions = driver.use_replica_exceptions
        self.isolation_read_committed = driver.ISOLATION_LEVEL_READ_COMMITTED
        self.isolation_serializable = driver.ISOLATION_LEVEL_SERIALIZABLE
        self.keep_history = options.keep_history
        self._db_connect_with_isolation = driver.connect_with_isolation
        super(Psycopg2ConnectionManager, self).__init__(options)

    def _alter_dsn(self, replica):
        """Alter the DSN to use the specified replica.

        The replica parameter is a string specifying either host or host:port.
        """
        if ':' in replica:
            host, port = replica.split(':')
            dsn = '%s host=%s port=%s' % (self._dsn, host, port)
        else:
            dsn = '%s host=%s' % (self._dsn, replica)
        return dsn

    @metricmethod
    def open(self, isolation=None, replica_selector=None, **kwargs):
        """Open a database connection and return (conn, cursor)."""
        # pylint:disable=arguments-differ
        if isolation is None:
            isolation = self.isolation_read_committed

        if replica_selector is None:
            replica_selector = self.replica_selector

        if replica_selector is not None:
            replica = replica_selector.current()
            dsn = self._alter_dsn(replica)
        else:
            replica = None
            dsn = self._dsn

        while True:
            try:
                conn, cursor = self._db_connect_with_isolation(isolation, dsn)
                cursor.arraysize = 64
                conn.replica = replica
                return conn, cursor
            except self.use_replica_exceptions as e:
                if replica is not None:
                    next_replica = replica_selector.next()
                    if next_replica is not None:
                        log.warning("Unable to connect to replica %s: %s, "
                                    "now trying %s", replica, e, next_replica)
                        replica = next_replica
                        dsn = self._alter_dsn(replica)
                        continue
                log.warning("Unable to connect: %s", e)
                raise

    def _do_open_for_load(self):
        """Open and initialize a connection for loading objects.

        Returns (conn, cursor).
        """
        # XXX: SERIALIZABLE isn't allowed on streaming replicas
        # (https://www.enterprisedb.com/blog/serializable-postgresql-11-and-beyond)
        # Do we really need SERIALIZABLE? Wouldn't REPEATABLE READ be
        # sufficient?
        #
        # TODO: Set the transaction to READ ONLY mode. This can be done with
        # a SET TRANSACTION command, or psycopg2 likes the 'conn.readonly' property
        # (2.7+) or conn.set_session().
        # TODO: Enable deferrable transactions if we stay in serializable, read only
        # mode. This should generally be faster, as the *only* serializable transactions
        # we have should be READ ONLY.
        return self.open(self.isolation_serializable,
                         replica_selector=self.ro_replica_selector)

    # TODO: Define _do_open_for_store to change the default isolation
    # level from READ COMMITTED to REPEATABLE READ? READ COMMITTED
    # takes a MVCC snapshot for every individual *statement* in the
    # transaction; REPEATABLE READ takes a snapshot for the whole
    # duration of the transaction, beginning at the first
    # SELECT/UPDATE/INSERT statement. We could get by with fewer locks
    # that way (see locker.py).
    # def _do_open_for_store(self):
    #    return self.open('REPEATABLE READ')
