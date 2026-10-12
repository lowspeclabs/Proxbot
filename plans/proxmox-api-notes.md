# Proxmox API Notes

## Base URL

Proxmox VE REST API calls generally use this base shape:

```text
https://<host>:8006/api2/json
```

## API Token Authentication

Use an Authorization header:

```text
Authorization: PVEAPIToken=USER@REALM!TOKENID=TOKEN_SECRET
```

Store these parts in `.env`:

```dotenv
PROXMOX_API_TOKEN_ID=USER@REALM!TOKENID
PROXMOX_API_TOKEN_SECRET=TOKEN_SECRET
```

The CLI should assemble the header at runtime and redact the secret in all logs.

## Useful Endpoint Patterns

```text
GET  /version
GET  /cluster/status
GET  /nodes
GET  /nodes/{node}/status
GET  /nodes/{node}/qemu
GET  /nodes/{node}/qemu/{vmid}/status/current
GET  /nodes/{node}/qemu/{vmid}/config
POST /nodes/{node}/qemu/{vmid}/status/start
POST /nodes/{node}/qemu/{vmid}/status/shutdown
POST /nodes/{node}/qemu/{vmid}/status/stop
GET  /nodes/{node}/lxc
GET  /nodes/{node}/lxc/{vmid}/status/current
GET  /nodes/{node}/storage
GET  /nodes/{node}/tasks
GET  /nodes/{node}/tasks/{upid}/status
GET  /nodes/{node}/tasks/{upid}/log
GET  /nodes/{node}/aplinfo
POST /nodes/{node}/aplinfo
```

## Container Template Registry (aplinfo)

`GET /nodes/{node}/aplinfo` (`pveam available`) takes **no query parameters** — section/search/limit filtering is done client-side. Entries carry `template`, `section` (`system`/`turnkeylinux`/`mail`), `package`, `os`, `version`, `location`, and checksums.

`POST /nodes/{node}/aplinfo` with `{storage, template}` (`pveam download <storage> <template>`) downloads a registry template by name and returns a task UPID. Requires `Datastore.AllocateTemplate` on the storage. There is **no REST endpoint for `pveam update`** (node-local only), so the registry reflects each node's cached DB.

## Task Behavior

Many write operations return a task ID. The CLI should capture that ID, log it, and support a wait command that polls task status until completion or timeout.

## LXC Console Quirk on Debian 13

The `debian-13-standard` LXC template ships **systemd 257**. Its `console-getty.service` includes `ImportCredential` directives that fail inside LXC containers with `status=243/CREDENTIALS`. As a result, the Proxmox web console opens but shows a blank screen because no login prompt spawns on `/dev/console`.

The CLI detects this template and prints a workaround after `lxc create`:

```bash
python scripts/Proxmox-cli.py lxc create --node pve1 --vmid 109 \
  --hostname ai-test-2 --ostemplate local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --storage local-lvm --execute --wait
```

The workaround must be applied on the Proxmox node (the CLI is a REST API wrapper and cannot run commands inside the container):

```bash
pct exec 109 -- mkdir -p /etc/systemd/system/console-getty.service.d
pct exec 109 -- sh -c 'echo -e "[Service]\nImportCredential=" > /etc/systemd/system/console-getty.service.d/override.conf'
pct exec 109 -- systemctl daemon-reload
pct exec 109 -- systemctl reset-failed console-getty.service
pct exec 109 -- systemctl start console-getty.service
```

You can also print the instructions at any time with:

```bash
python scripts/Proxmox-cli.py lxc fix-console --node pve1 --vmid 109
```

Prefer dedicated API users and scoped API tokens. Avoid using root tokens unless this is a disposable lab.

## SSL Notes

SSL verification should default to enabled. Homelabs may use self-signed certificates, but disabling verification should produce a visible warning.
