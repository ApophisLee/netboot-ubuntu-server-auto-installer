# ubuntu-server-autoinstall

## Updating Ubuntu

Run the **Update Ubuntu release** workflow from the repository's Actions tab.
It finds the newest supported Ubuntu Server LTS release that has matching AMD64
and ARM64 netboot.xyz assets, validates every download URL, updates `main.ipxe`,
and opens a pull request for review.
