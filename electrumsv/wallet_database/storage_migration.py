"""
Keeps backwards compatible logic for storage migration.
"""
# TODO(no-merge) write a decision document for why we have this file.
import concurrent
from enum import IntFlag as _IntFlag
import json
try:
    # Linux expects the latest package version of 3.34.0 (as of pysqlite-binary 0.4.5)
    import pysqlite3 as sqlite3
except ModuleNotFoundError:
    # MacOS has latest brew version of 3.34.0 (as of 2021-01-13).
    # Windows builds use the official Python 3.9.1 builds and bundled version of 3.33.0.
    import sqlite3 # type: ignore
import time
from typing import Any, Iterable, NamedTuple, Optional, Sequence

from ..constants import DerivationType, KeyInstanceFlag, PaymentFlag, ScriptType

from .sqlite_support import DatabaseContext, replace_db_context_with_connection
from .util import get_timestamp


# https://bugs.python.org/issue41907
class IntFlag(_IntFlag):
    def __format__(self, spec):
        return format(self.value, spec)


class TransactionOutputFlag1(IntFlag):
    NONE = 0

    # If the UTXO is in a local or otherwise unconfirmed transaction.
    IS_ALLOCATED = 1 << 1
    # If the UTXO is in a confirmed transaction.
    IS_SPENT = 1 << 2
    # If the UTXO is marked as not to be used. It should not be allocated if unallocated, and
    # if allocated then ideally we might extend this to prevent further dispatch in any form.
    IS_FROZEN = 1 << 3
    IS_COINBASE = 1 << 4

    USER_SET_FROZEN = 1 << 8

    FROZEN_MASK = IS_FROZEN | USER_SET_FROZEN


class TxFlags1(IntFlag):
    HasFee = 1 << 4
    HasHeight = 1 << 5
    HasPosition = 1 << 6
    HasByteData = 1 << 12

    # A transaction received over the p2p network which is unconfirmed and in the mempool.
    STATE_CLEARED = 1 << 20
    # A transaction received over the p2p network which is confirmed and known to be in a block.
    STATE_SETTLED = 1 << 21

    METADATA_FIELD_MASK = (HasFee | HasHeight | HasPosition)


class AccountRow1(NamedTuple):
    account_id: int
    default_masterkey_id: Optional[int]
    default_script_type: ScriptType
    account_name: str


class KeyInstanceRow1(NamedTuple):
    keyinstance_id: int
    account_id: int
    masterkey_id: Optional[int]
    derivation_type: DerivationType
    derivation_data: bytes
    script_type: ScriptType
    flags: KeyInstanceFlag
    description: Optional[str]


class MasterKeyRow1(NamedTuple):
    masterkey_id: int
    parent_masterkey_id: Optional[int]
    derivation_type: DerivationType
    derivation_data: bytes


class PaymentRequestRow1(NamedTuple):
    paymentrequest_id: int
    keyinstance_id: int
    state: PaymentFlag
    value: Optional[int]
    expiration: Optional[int]
    description: Optional[str]
    date_created: int


class TransactionOutputRow1(NamedTuple):
    tx_hash: bytes
    tx_index: int
    value: int
    keyinstance_id: Optional[int]
    flags: TransactionOutputFlag1


class TxData1(NamedTuple):
    height: Optional[int] = None
    position: Optional[int] = None
    fee: Optional[int] = None
    date_added: Optional[int] = None
    date_updated: Optional[int] = None

    def __repr__(self):
        return (f"TxData1(height={self.height},position={self.position},fee={self.fee},"
            f"date_added={self.date_added},date_updated={self.date_updated})")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TxData1):
            return NotImplemented
        return (self.height == other.height and self.position == other.position
            and self.fee == other.fee)


class TxProof1(NamedTuple):
    position: int
    branch: Sequence[bytes]


class TransactionRow1(NamedTuple):
    tx_hash: bytes
    tx_data: TxData1
    tx_bytes: Optional[bytes]
    flags: TxFlags1
    description: Optional[str]
    version: Optional[int]
    locktime: Optional[int]


class WalletDataRow1(NamedTuple):
    key: str
    value: Any


def create_accounts1(db_context: DatabaseContext, entries: Iterable[AccountRow1]) \
        -> concurrent.futures.Future:
    timestamp = get_timestamp()
    datas = [ (*t, timestamp, timestamp) for t in entries ]
    query = ("INSERT INTO Accounts (account_id, default_masterkey_id, default_script_type, "
        "account_name, date_created, date_updated) VALUES (?, ?, ?, ?, ?, ?)")
    def _write(db: sqlite3.Connection):
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_keys1(db_context: DatabaseContext, entries: Iterable[KeyInstanceRow1]) \
        -> concurrent.futures.Future:
    timestamp = int(time.time())
    datas = [ (*t, timestamp, timestamp) for t in entries]
    query = ("INSERT INTO KeyInstances (keyinstance_id, account_id, masterkey_id, "
        "derivation_type, derivation_data, script_type, flags, description, date_created, "
        "date_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    def _write(db: sqlite3.Connection):
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_master_keys1(db_context: DatabaseContext, entries: Iterable[MasterKeyRow1]) \
        -> concurrent.futures.Future:
    timestamp = get_timestamp()
    datas = [ (*t, timestamp, timestamp) for t in entries ]
    query = ("INSERT INTO MasterKeys (masterkey_id, parent_masterkey_id, derivation_type, "
        "derivation_data, date_created, date_updated) VALUES (?, ?, ?, ?, ?, ?)")
    def _write(db: sqlite3.Connection):
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_payment_requests1(db_context: DatabaseContext, entries: Iterable[PaymentRequestRow1]) \
        -> concurrent.futures.Future:
    # Duplicate the last column for date_updated = date_created
    query = ("INSERT INTO PaymentRequests "
        "(paymentrequest_id, keyinstance_id, state, value, expiration, description, date_created, "
        "date_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    datas = [ (*t, t[-1]) for t in entries ]
    def _write(db: sqlite3.Connection):
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_transaction_outputs1(db_context: DatabaseContext,
        entries: Iterable[TransactionOutputRow1]) -> concurrent.futures.Future:
    timestamp = int(time.time())
    datas = [ (*t, timestamp, timestamp) for t in entries ]
    query = ("INSERT INTO TransactionOutputs (tx_hash, tx_index, value, keyinstance_id, "
        "flags, date_created, date_updated) VALUES (?, ?, ?, ?, ?, ?, ?)")
    def _write(db: sqlite3.Connection):
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_transactions1(db_context: DatabaseContext, entries: Iterable[TransactionRow1]) \
        -> concurrent.futures.Future:
    query = ("INSERT INTO Transactions (tx_hash, tx_data, flags, "
        "block_height, block_position, fee_value, description, version, locktime, "
        "date_created, date_updated) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)")

    datas = []
    for tx_hash, metadata, bytedata, flags, description, version, locktime in entries:
        assert type(tx_hash) is bytes and bytedata is not None
        assert (flags & TxFlags1.HasByteData) == 0, "this flag is not applicable"
        flags &= ~TxFlags1.METADATA_FIELD_MASK
        if metadata.height is not None:
            flags |= TxFlags1.HasHeight
        if metadata.fee is not None:
            flags |= TxFlags1.HasFee
        if metadata.position is not None:
            flags |= TxFlags1.HasPosition
        assert metadata.date_added is not None and metadata.date_updated is not None
        datas.append((tx_hash, bytedata, flags, metadata.height, metadata.position,
            metadata.fee, description, version, locktime, metadata.date_added,
            metadata.date_updated))

    def _write(db: sqlite3.Connection) -> None:
        nonlocal query, datas
        db.executemany(query, datas)
    return db_context.post_to_thread(_write)


def create_wallet_datas1(db_context: DatabaseContext, entries: Iterable[WalletDataRow1]) \
        -> concurrent.futures.Future:
    sql = ("INSERT INTO WalletData (key, value, date_created, date_updated) "
        "VALUES (?, ?, ?, ?)")
    timestamp = get_timestamp()
    rows = []
    for entry in entries:
        assert type(entry.key) is str, f"bad key '{entry.key}'"
        data = json.dumps(entry.value)
        rows.append([ entry.key, data, timestamp, timestamp])

    def _write(db: sqlite3.Connection) -> None:
        nonlocal sql, rows
        db.executemany(sql, rows)
    return db_context.post_to_thread(_write)


@replace_db_context_with_connection
def read_wallet_data1(db: sqlite3.Connection, key: str) -> Any:
    sql = "SELECT value FROM WalletData WHERE key=?"
    cursor = db.execute(sql, (key,))
    row = cursor.fetchone()
    return json.loads(row[0]) if row is not None else None


def update_wallet_datas1(db_context: DatabaseContext, entries: Iterable[WalletDataRow1]) \
        -> concurrent.futures.Future:
    sql = "UPDATE WalletData SET value=?, date_updated=? WHERE key=?"
    timestamp = get_timestamp()
    rows = []
    for entry in entries:
        rows.append((json.dumps(entry.value), timestamp, entry.key))

    def _write(db: sqlite3.Connection) -> None:
        nonlocal sql, rows
        db.executemany(sql, rows)
    return db_context.post_to_thread(_write)