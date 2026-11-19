# headscale-update-ipv4

A Python script that changes the Tailscale IPv4 address of a single Headscale node in SQLite.

## Database fields

- `nodes.id` identifies the target node by its numeric ID.
- `nodes.given_name` identifies the node in the preview and in address conflict messages.
- `nodes.ipv4` holds the current address and is checked for conflicts. This is the only field the script updates.
- `nodes.deleted_at` excludes deleted nodes from selection.

## Usage

From the Docker Compose project directory, list the nodes and find the numeric ID:

```sh
docker compose exec -T headscale headscale nodes list
```

Run `python3 headscale-ip-update.py NODE_ID NEW_IPV4` for a read-only preview. The script checks that the address is usable within the configured IPv4 prefix and available.

Add `--apply` to stop Headscale if it is running, create and verify a SQLite backup, and update the selected node in a transaction. The script starts Headscale again if it was running at the start.

For another deployment, adjust the database path, IPv4 prefix, and container name at the top of the script.
