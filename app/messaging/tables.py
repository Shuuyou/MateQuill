"""Control-plane locators. Message content lives in scoped resource bodies."""
from sqlalchemy import Boolean, Column, Float, Integer, String, Table
from app.tenancy.database import metadata

bindings = Table('mq_messaging_bindings', metadata,
    Column('id', String(64), primary_key=True), Column('channel', String(24), nullable=False),
    Column('connection_id', String(128), nullable=False), Column('identity_hash', String(64), unique=True, nullable=False),
    Column('user_id', String(64), nullable=False), Column('tenant_id', String(64), nullable=False),
    Column('store_id', String(120)), Column('session_hash', String(64), nullable=False),
    Column('active', Boolean, nullable=False, default=True), Column('created_at', Float, nullable=False),
    Column('last_seen_at', Float, nullable=False))
codes = Table('mq_messaging_binding_codes', metadata,
    Column('digest', String(64), primary_key=True), Column('purpose', String(20), nullable=False),
    Column('channel', String(24), nullable=False), Column('user_id', String(64), nullable=False),
    Column('tenant_id', String(64), nullable=False), Column('store_id', String(120)),
    Column('session_hash', String(64), nullable=False), Column('conversation_id', String(64)),
    Column('created_at', Float, nullable=False), Column('expires_at', Float, nullable=False), Column('used_at', Float))
inbox = Table('mq_messaging_inbox', metadata,
    Column('id', String(64), primary_key=True), Column('binding_id', String(64), nullable=False),
    Column('channel', String(24), nullable=False), Column('status', String(24), nullable=False),
    Column('conversation_id', String(64)), Column('turn_id', String(64)),
    Column('claim_token', String(64)), Column('lease_until', Float, nullable=False, default=0),
    Column('attempts', Integer, nullable=False, default=0), Column('created_at', Float, nullable=False),
    Column('updated_at', Float, nullable=False))
outbox = Table('mq_messaging_outbox', metadata,
    Column('id', String(64), primary_key=True), Column('binding_id', String(64), nullable=False),
    Column('channel', String(24), nullable=False), Column('status', String(24), nullable=False),
    Column('message_id', String(256)), Column('attempts', Integer, nullable=False, default=0),
    Column('claim_token', String(64)), Column('lease_until', Float, nullable=False, default=0),
    Column('next_attempt_at', Float, nullable=False, default=0),
    Column('created_at', Float, nullable=False), Column('updated_at', Float, nullable=False))
threads = Table('mq_messaging_threads', metadata,
    Column('id', String(64), primary_key=True), Column('binding_id', String(64), nullable=False),
    Column('conversation_id', String(64), nullable=False), Column('updated_at', Float, nullable=False))
connections = Table('mq_messaging_connections', metadata,
    Column('id', String(128), primary_key=True), Column('channel', String(24), nullable=False),
    Column('status', String(32), nullable=False), Column('heartbeat_at', Float, nullable=False),
    Column('error_code', String(64)))
TABLES = (bindings, codes, inbox, outbox, threads, connections)
