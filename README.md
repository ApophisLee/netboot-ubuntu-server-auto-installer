# ubuntu-server-autoinstall

## Post-install provisioning

`user-data` carries a nested `autoinstall.user-data` block that cloud-init
replays on the installed system's first boot. The install itself ends in
`poweroff`, and Docker needs a booted system with systemd and a network, so
none of this can run from `late-commands` inside the installer.

The provisioning script extends the root logical volume by 80% of the free
extents that guided LVM leaves in `ubuntu-vg`, installs the latest
`docker-ce` from Docker's own repository, enables a single-node swarm, and
deploys Portainer CE from `/etc/docker/portainer-agent-stack.yml`.
`/etc/docker/daemon.json` caps json-file logs at three 10 MB files and is
written before Docker installs, so it applies from the daemon's first start.

Portainer serves its UI on ports 9443 and 9000, with the edge agent tunnel
on 8000. No firewall rules are added, and `ubuntu` is deliberately left out
of the `docker` group. The script is idempotent: run
`sudo /usr/local/sbin/post-install-setup.sh` to finish provisioning a
machine whose first boot came up without a network.

## Updating Ubuntu

Run the **Update Ubuntu release** workflow from the repository's Actions tab.
It finds the newest supported Ubuntu Server LTS release that has matching AMD64
and ARM64 netboot.xyz assets, validates every download URL, updates `main.ipxe`,
and opens a pull request for review.
