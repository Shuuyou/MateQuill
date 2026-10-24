# Messaging workers

Enterprise WeChat, Feishu and DingTalk use their official long-connection SDKs.
They share the web application's GTM conversations, artifact versions, tenant
authorization and durable Agent queue. QQ and personal WeChat are not enabled.

Install `requirements-messaging.txt`, apply `alembic upgrade head` with the
migration role, then launch a separately supervised worker:

```powershell
python scripts/run_messaging.py --channel wecom --env-file runtime/messaging.env
```

The ignored environment file must contain the channel credentials **and the same
`MATEQUILL_DATABASE_URL` as the web backend**. The runner requires this setting
explicitly. Use the application role, never the migration owner. Supply the same
model configuration as the web backend through the worker's environment.
Set `MATEQUILL_MESSAGING_WEB_ORIGIN` to the actual web frontend origin; localhost
links work only on the development computer. Public deployments use HTTPS.

Incoming private messages create an independent trial workspace when no binding
exists. A web user can generate a single-use binding code to continue an existing
conversation in chat. Completed manuscripts are returned with their actual
selected images. `继续编辑`, `打开网页` and `重新生成链接` retrieve the current
artifact without another model call. Browser handoff links last 15 minutes and
require explicit confirmation; switching an existing browser workspace requires
an additional explicit switch flag and a clearly labelled button.

Enterprise WeChat images use native media init/chunk/finish and image reply.
Chunk indices are zero-based, matching the official Node SDK implementation.
Replies remain attached to the incoming callback. Logs contain connection state,
operation names and numeric platform codes, never credentials or message bodies.

Connection badges require authentication and a recent heartbeat. An ACK alone
does not establish that a particular client rendered an image; verify the actual
client during channel acceptance. Inbox/outbox records track retry state and
delivery. The frontend and worker must be migrated and deployed together.

Regression checks:

```powershell
python -m pytest tests/test_messaging_gateway.py tests/test_messaging_transports.py tests/test_gtm_agent_core.py
```
