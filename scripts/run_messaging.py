"""Run an official, separately supervised messaging connection.

Example: python scripts/run_messaging.py --channel wecom --env-file runtime/messaging.env
"""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def run(channel):
    database=os.getenv('MATEQUILL_DATABASE_URL','').strip()
    if not database:
        raise SystemExit('Set MATEQUILL_DATABASE_URL explicitly to the same database as the web backend before starting a messaging worker.')
    from sqlalchemy.engine import make_url
    try:
        backend=make_url(database).get_backend_name()
        if backend not in {'postgresql','sqlite'}:
            raise ValueError
    except Exception:
        raise SystemExit('Messaging database configuration is invalid.') from None
    from app.tenancy.database import db_connection
    from sqlalchemy import text
    try:
        with db_connection() as connection:
            connection.execute(text('SELECT 1'))
    except Exception:
        raise SystemExit('Messaging database is unavailable. Check the configured backend and migrations.') from None
    logging.getLogger(__name__).info('messaging database type: %s',backend)
    from app.messaging.config import channel_configs,sdk_available
    config=channel_configs()[channel]
    if not config.configured:
        raise SystemExit('Channel is unconfigured. Add credentials to the local ignored env file.')
    if not sdk_available(config):
        raise SystemExit('Install requirements-messaging.txt before starting this channel.')
    from app.messaging.gateway import Gateway
    from app.messaging.wecom import WeComTransport
    from app.messaging.enterprise import FeishuTransport,DingTalkTransport
    from app.gtm_agent.jobs import start_agent_workers,stop_agent_workers
    if channel=='qq':
        raise SystemExit('This transport is not enabled yet. Use the configured Enterprise WeChat entry.')
    transport={'wecom':WeComTransport,'feishu':FeishuTransport,'dingtalk':DingTalkTransport}[channel](config)
    gateway=Gateway(transport)
    await start_agent_workers()
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(transport.run(gateway))
            tasks.create_task(gateway.run())
    finally:
        await stop_agent_workers()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=('wecom','feishu','dingtalk','qq'),required=True)
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    if args.env_file:
        path=args.env_file.resolve()
        runtime=(ROOT/'runtime').resolve()
        if runtime not in path.parents or not path.is_file():
            raise SystemExit('Use an existing ignored env file inside this workspace runtime directory.')
        from dotenv import load_dotenv
        load_dotenv(path,override=False)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('websockets').setLevel(logging.WARNING)
    try:
        asyncio.run(run(args.channel))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.getLogger(__name__).error('messaging worker stopped (%s)',type(exc).__name__)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
