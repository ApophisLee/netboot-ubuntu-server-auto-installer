#bin/bash
make CONFIG=cloud bin/ipxe.lkrn bin-x86_64-efi/ipxe.efi
make CONFIG=cloud bin-arm64-efi/ipxe.efi CROSS=aarch64-linux-gnu-
./util/genfsimg -o bin/ipxe.iso -s autoexec.ipxe bin/ipxe.lkrn bin-x86_64-efi/ipxe.efi bin-arm64-efi/ipxe.efi