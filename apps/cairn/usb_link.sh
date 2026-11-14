#!/usr/bin/env bash
# BOARD (sudo): USB-C NCM network gadget, link-local only, no forwarding. Undo: echo "" > $G/UDC
set -eu
modprobe libcomposite
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
G=/sys/kernel/config/usb_gadget/cairn
mkdir -p $G && cd $G
echo 0x1d6b > idVendor; echo 0x0104 > idProduct
mkdir -p strings/0x409; echo cairn-iq9 > strings/0x409/serialnumber; echo Cairn > strings/0x409/manufacturer; echo "IQ9 USB link" > strings/0x409/product
mkdir -p configs/c.1 functions/ncm.usb0
[ -e configs/c.1/ncm.usb0 ] || ln -s functions/ncm.usb0 configs/c.1/
# a600000 is the ADB USB-C port on our EVK
UDC_NAME="${1:-a600000.usb}"; echo "$UDC_NAME" > UDC
sleep 2; IF=$(cat functions/ncm.usb0/ifname); ip link set "$IF" up
ip addr add 169.254.77.1/16 dev "$IF" 2>/dev/null || true  # laptop self-assigns 169.254.x
echo "USB-LINK-OK $IF $UDC_NAME"; ip -br addr show "$IF"
